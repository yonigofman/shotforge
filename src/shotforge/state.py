"""Runtime state and the approval log, both under ``.shotforge/``.

The YAML files hold what the film is; this holds where the work stands. Every
approval is appended to ``approvals.jsonl`` with an optional note, so you can
always go back and see why something was decided.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from shotforge.paths import ProjectPaths


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class ShotState:
    """Per-shot runtime facts that are not part of the shot spec."""

    frame_pass: int = 1
    active_take: dict[str, int] = field(default_factory=dict)
    updated: str = ""


@dataclass
class ApprovalEntry:
    ts: str
    scene: str
    shot: str
    action: str
    from_status: str
    to_status: str
    note: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class ProjectState:
    """Reads and writes ``.shotforge/state.json`` and appends to the approval log."""

    def __init__(self, paths: ProjectPaths):
        self.paths = paths
        self._data: dict[str, Any] = {"active": {}, "shots": {}}
        self.reload()

    # -- persistence -------------------------------------------------------
    def reload(self) -> None:
        if self.paths.state_file.is_file():
            try:
                self._data = json.loads(self.paths.state_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # A corrupt state file must never block the project: the YAML
                # files are the source of truth and state is rebuildable.
                self._data = {"active": {}, "shots": {}}
        self._data.setdefault("active", {})
        self._data.setdefault("shots", {})

    def save(self) -> None:
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.paths.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.paths.state_file)

    # -- active shot -------------------------------------------------------
    @property
    def active(self) -> tuple[str, str] | None:
        active = self._data.get("active") or {}
        if active.get("scene") and active.get("shot"):
            return active["scene"], active["shot"]
        return None

    def set_active(self, scene_id: str, shot_id: str) -> None:
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
            updated=raw.get("updated", ""),
        )

    def update_shot(self, scene_id: str, shot_id: str, **changes: Any) -> ShotState:
        state = self.shot(scene_id, shot_id)
        for name, value in changes.items():
            if not hasattr(state, name):
                raise KeyError(f"unknown shot state field {name!r}")
            setattr(state, name, value)
        state.updated = _now()
        self._data["shots"][self.key(scene_id, shot_id)] = asdict(state)
        self.save()
        return state

    # -- approval log ------------------------------------------------------
    def log_approval(self, entry: ApprovalEntry) -> None:
        self.paths.state_dir.mkdir(parents=True, exist_ok=True)
        with self.paths.approvals_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

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
