"""Entity prompts, inbox filing and the Bible's design approval gate."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from shotforge import inbox
from shotforge.models.common import Entity, as_plain
from shotforge.pipeline import TransitionError
from shotforge.project import Project, ProjectError
from shotforge.skills import find_skill, render_prompt
from shotforge.state import ApprovalEntry, ProjectState
from shotforge.yamlio import write_text

_mutations = Lock()


def save_entity(
    project: Project, state: ProjectState, kind: str, text: str, entity_id: str | None = None
) -> Entity:
    entity = project.parse_entity(kind, text, entity_id)
    path = project.bible_entity_path(kind, entity.id)
    with _mutations:
        previous = project.entity(kind, entity_id) if entity_id is not None else None
        try:
            write_text(path, text, create_only=entity_id is None)
        except FileExistsError as exc:
            raise ProjectError(f"{kind.capitalize()} {entity.id!r} already exists.") from exc
        if previous != entity:
            state.set_design_approval(kind, entity.id, None)
    return entity


def file_asset(
    project: Project,
    state: ProjectState,
    kind: str,
    entity_id: str,
    asset: inbox.DesignAsset,
    source: Path,
) -> None:
    with _mutations:
        inbox.file_to_design(project, source, kind, entity_id, asset)
        state.set_design_approval(kind, entity_id, None)


def _digest(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def revision(project: Project, kind: str, entity_id: str, entity: Entity) -> str | None:
    """Identify the sheet, ref and authored design shown at the approval gate."""
    images = {}
    for asset in inbox.DesignAsset:
        try:
            with asset.path(project.paths, kind, entity_id).open("rb") as image:
                images[asset.value] = hashlib.file_digest(image, "sha256").hexdigest()
        except (FileNotFoundError, IsADirectoryError):
            return None
    return _digest({"entity": as_plain(entity), "images": images})


def approve(
    project: Project,
    state: ProjectState,
    kind: str,
    entity_id: str,
    expected: str,
    note: str | None = None,
) -> None:
    with _mutations:
        entity = project.entity(kind, entity_id)
        current = revision(project, kind, entity_id, entity)
        if current is None:
            raise TransitionError("File both the sheet and clean reference before approving.")
        if current != expected:
            raise TransitionError("The design changed. Review the current images before approving.")
        state.reload()
        if state.approved_design_revision(kind, entity_id) == current:
            return
        state.set_design_approval(kind, entity_id, current)
        state.log_approval(
            ApprovalEntry(
                ts=datetime.now(UTC).isoformat(timespec="seconds"),
                scene=None,
                shot=None,
                action="approve",
                from_status="design",
                to_status="designed",
                note=note,
                entity_kind=kind,
                entity_id=entity_id,
            )
        )


def _prompt_context(project: Project, kind: str, entity: Entity) -> dict:
    return {"kind": kind, "entity": as_plain(entity), "style": as_plain(project.style())}


def _prompt_path(project: Project, kind: str, entity_id: str) -> Path:
    return project.paths.design_dir(kind, entity_id) / "prompts" / "chatgpt-sheet.json"


def build_prompts(project: Project, kind: str, entity_id: str) -> None:
    """Render a craft brief once, then adapt it to two ChatGPT image prompts."""
    with _mutations:
        entity = project.entity(kind, entity_id)
        context = _prompt_context(project, kind, entity)
        craft = find_skill(f"{kind}-design", project.paths.skills)
        adapter = find_skill("chatgpt-sheet", project.paths.skills)
        brief = render_prompt(craft, context)
        prompts = {
            asset.value: render_prompt(adapter, {**context, "brief": brief, "asset": asset.value})
            for asset in inbox.DesignAsset
        }
        snapshot = {"input": _digest(context), "prompts": prompts}
        text = json.dumps(snapshot, ensure_ascii=False, indent=2)
        write_text(_prompt_path(project, kind, entity_id), text)


def current_prompts(project: Project, kind: str, entity_id: str, entity: Entity) -> dict[str, str]:
    """Show the saved pair only if it describes the current Bible and style."""
    try:
        saved = json.loads(_prompt_path(project, kind, entity_id).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if isinstance(saved, dict) and saved.get("input") == _digest(
        _prompt_context(project, kind, entity)
    ):
        return saved.get("prompts", {})
    return {}
