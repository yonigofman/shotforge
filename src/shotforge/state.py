"""Runtime state and the approval log, both under ``.shotforge/``.

The YAML files hold what the film is; this holds where the work stands. Every
approval is appended to ``approvals.jsonl`` with an optional note, so you can
always go back and see why something was decided.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from shotforge.paths import ProjectPaths
from shotforge.yamlio import write_text

_state_lock = Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class ShotState:
    """Per-shot runtime facts that are not part of the shot spec."""

    frame_pass: int = 1
    active_take: dict[str, int] = field(default_factory=dict)
    #: Take numbers you set aside, by kind. The files stay; they stop being chosen.
    rejected: dict[str, list[int]] = field(default_factory=dict)
    updated: str = ""


@dataclass
class ApprovalEntry:
    ts: str
    scene: str | None
    shot: str | None
    action: str
    from_status: str
    to_status: str
    note: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    entity_kind: str | None = None
    entity_id: str | None = None
    #: For an ``unapprove``: the ``ts`` of the approval it takes back.
    undoes: str | None = None

    @property
    def subject(self) -> str:
        if self.entity_kind:
            return f"{self.entity_kind}/{self.entity_id}"
        return f"{self.scene}/{self.shot}"


class ProjectState:
    """Reads and writes ``.shotforge/state.json`` and appends to the approval log."""

    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self._data: dict[str, Any]
        self.reload()

    # -- persistence -------------------------------------------------------
    def reload(self) -> None:
        try:
            data = json.loads(self.paths.state_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            # A missing or corrupt state file must never block the project.
            data = {}
        self._data = data if isinstance(data, dict) else {}
        self._data.setdefault("active", {})
        self._data.setdefault("shots", {})
        self._data.setdefault("designs", {})

    def save(self) -> None:
        write_text(self.paths.state_file, json.dumps(self._data, indent=2, ensure_ascii=False))

    # -- active shot -------------------------------------------------------
    @property
    def active(self) -> tuple[str, str] | None:
        active = self._data.get("active") or {}
        if active.get("scene") and active.get("shot"):
            return active["scene"], active["shot"]
        return None

    def set_active(self, scene_id: str, shot_id: str) -> None:
        with _state_lock:
            self.reload()
            self._data["active"] = {"scene": scene_id, "shot": shot_id, "since": _now()}
            self.save()

    # -- per-shot state ----------------------------------------------------
    @staticmethod
    def key(scene_id: str, shot_id: str) -> str:
        return f"{scene_id}/{shot_id}"

    def shot(self, scene_id: str, shot_id: str) -> ShotState:
        raw = self._data["shots"].get(self.key(scene_id, shot_id)) or {}
        return ShotState(
            frame_pass=int(raw.get("frame_pass", 1)),
            active_take=dict(raw.get("active_take", {})),
            rejected={kind: list(numbers) for kind, numbers in (raw.get("rejected") or {}).items()},
            updated=raw.get("updated", ""),
        )

    def update_shot(self, scene_id: str, shot_id: str, **changes: Any) -> ShotState:
        with _state_lock:
            self.reload()
            state = self.shot(scene_id, shot_id)
            for name, value in changes.items():
                if not hasattr(state, name):
                    raise KeyError(f"unknown shot state field {name!r}")
                setattr(state, name, value)
            state.updated = _now()
            self._data["shots"][self.key(scene_id, shot_id)] = asdict(state)
            self.save()
            return state

    # -- rejected takes ----------------------------------------------------
    def rejected_takes(self, scene_id: str, shot_id: str, kind: str) -> set[int]:
        """Take numbers set aside for this kind. Nothing is deleted."""
        return set(self.shot(scene_id, shot_id).rejected.get(str(kind), []))

    def set_take_rejected(
        self, scene_id: str, shot_id: str, kind: str, number: int, rejected: bool
    ) -> set[int]:
        state = self.shot(scene_id, shot_id)
        numbers = set(state.rejected.get(str(kind), []))
        if rejected:
            numbers.add(number)
        else:
            numbers.discard(number)
        by_kind = dict(state.rejected)
        if numbers:
            by_kind[str(kind)] = sorted(numbers)
        else:
            by_kind.pop(str(kind), None)
        self.update_shot(scene_id, shot_id, rejected=by_kind)
        return numbers

    # -- per-entity design -------------------------------------------------
    def approved_design_revision(self, kind: str, entity_id: str) -> str | None:
        raw = self._data["designs"].get(self.key(kind, entity_id), {})
        return raw.get("revision") if raw.get("designed") else None

    def set_design_approval(self, kind: str, entity_id: str, revision: str | None) -> None:
        with _state_lock:
            self.reload()
            designs = self._data["designs"]
            key = self.key(kind, entity_id)
            if revision:
                designs[key] = {"designed": True, "revision": revision}
            elif key in designs:
                designs.pop(key)
            else:
                return
            self.save()

    # -- approval log ------------------------------------------------------
    def log_approval(self, entry: ApprovalEntry) -> None:
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        record = asdict(entry)
        if entry.entity_kind is None:
            del record["entity_kind"], record["entity_id"]
        if entry.undoes is None:
            del record["undoes"]
        with self.paths.approvals_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def approvals(
        self, scene_id: str | None = None, shot_id: str | None = None, limit: int | None = None
    ) -> list[ApprovalEntry]:
        path = self.paths.approvals_log
        if not path.is_file():
            return []
        entries: list[ApprovalEntry] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            if scene_id and raw.get("scene") != scene_id:
                continue
            if shot_id and raw.get("shot") != shot_id:
                continue
            entries.append(ApprovalEntry(**raw))
        entries.reverse()
        return entries[:limit] if limit else entries
