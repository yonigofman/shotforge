"""What to do next, and the one frame that shows where the shot stands.

The home screen's job is not to list the project. It is to answer "what now",
so this works that out once and the page only has to draw it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shotforge.actions import stage_label
from shotforge.models import Stage
from shotforge.pipeline import next_action
from shotforge.project import Project
from shotforge.state import ProjectState
from shotforge.takes import TakeKind, list_takes


@dataclass(frozen=True)
class NextUp:
    """The shot the director should open, and why this one."""

    scene: str
    shot: str
    stage: str
    title: str
    tool: str
    detail: str
    reason: str
    thumb: Path | None


def pick_shot(project: Project, state: ProjectState) -> tuple[str, str, str] | None:
    """The active shot, else the first one still open. Scene, shot, and why."""
    active = state.active
    if active:
        scene_id, shot_id = active
        if shot_id in project.shot_ids(scene_id):
            return scene_id, shot_id, "the shot you made active"
    for scene_id in project.scene_ids():
        for shot in project.shots(scene_id):
            if shot.status.stage is not Stage.done:
                return scene_id, shot.id, "the first shot still open"
    return None


def thumbnail(project: Project, scene_id: str, shot_id: str) -> Path | None:
    """The newest thing to look at: what was approved, else the latest take,
    else the blockout. None when the shot has produced no picture yet."""
    paths = project.paths.shot(scene_id, shot_id)
    for path in (paths.approved_frame, paths.last_frame):
        if path.is_file():
            return path
    frames = list_takes(paths, TakeKind.frame)
    if frames:
        return frames[-1].path
    blockout = project.paths.blockout(scene_id, shot_id)
    return blockout if blockout.is_file() else None


def next_up(project: Project, state: ProjectState) -> NextUp | None:
    """Everything the home screen needs about the next move, or None when the
    project has no shots left to work on."""
    picked = pick_shot(project, state)
    if picked is None:
        return None
    scene_id, shot_id, reason = picked
    action = next_action(project, state, scene_id, shot_id)
    shot = project.shot(scene_id, shot_id)
    return NextUp(
        scene=scene_id,
        shot=shot_id,
        stage=stage_label(shot),
        title=action.title,
        tool=action.tool,
        detail=action.detail,
        reason=reason,
        thumb=thumbnail(project, scene_id, shot_id),
    )
