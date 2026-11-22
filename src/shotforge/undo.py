"""Taking an approval back.

You are the director, and a call you cannot take back is not really a call.
Rewinding the status alone is not enough: an approval also writes the fixed
names the later stages read — ``frames/first.png``, ``video/shot.mp4``,
``last_frame.png`` and the face crops cut from the approved frame. Undoing
removes exactly those, and nothing else.

The numbered takes are never touched. They are the record of what was tried,
and the reason two frames can never overwrite each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from shotforge.models import Shot, ShotStatus, Stage
from shotforge.paths import ProjectPaths
from shotforge.pipeline import ORDER, STAGE_LABEL, TransitionError
from shotforge.project import Project
from shotforge.state import ApprovalEntry, ProjectState


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class UndoPlan:
    """What taking the last approval back would do, before it is done."""

    stage: Stage
    #: The status the shot returns to — the work state of the approved stage.
    target: ShotStatus
    #: Files the approval wrote, that exist now and would be removed.
    removes: list[Path] = field(default_factory=list)
    #: What elsewhere in the project was built on this approval.
    dependents: list[str] = field(default_factory=list)
    #: The approval being taken back, from the log.
    approval: ApprovalEntry | None = None

    @property
    def label(self) -> str:
        return STAGE_LABEL[self.stage]

    @property
    def approved_at(self) -> str | None:
        return self.approval.ts if self.approval else None


def _last_approved(status: ShotStatus) -> ShotStatus:
    """The newest gate that still holds at this status."""
    reached = ORDER[: ORDER.index(status) + 1]
    approved = [s for s in reached if s.value.endswith("_approved")]
    if not approved:
        raise TransitionError("nothing has been approved on this shot yet")
    return approved[-1]


def can_undo(status: ShotStatus) -> bool:
    """True once any gate on this shot has closed."""
    return any(step.value.endswith("_approved") for step in ORDER[: ORDER.index(status) + 1])


def _artifacts(paths: ProjectPaths, scene_id: str, shot: Shot, stage: Stage) -> list[Path]:
    """The files that approving this stage wrote, under their fixed names."""
    shot_paths = paths.shot(scene_id, shot.id)
    if stage is Stage.frame:
        files = [shot_paths.approved_frame]
        files += [paths.face_crop(scene_id, shot.id, char) for char in shot.characters]
        return files
    if stage is Stage.video:
        return [shot_paths.approved_video, shot_paths.last_frame]
    # Breakdown approves the spec you already edited; the blockout render is
    # Blender's, not the gate's. Neither wrote a file to take back.
    return []


def _dependents(project: Project, scene_id: str, shot: Shot, stage: Stage) -> list[str]:
    """What is already standing on this approval, in the reader's words."""
    found: list[str] = []
    paths = project.paths
    shot_paths = paths.shot(scene_id, shot.id)
    if stage is Stage.video:
        following = project.next_shot(scene_id, shot.id)
        if following is not None and shot_paths.last_frame.is_file():
            line = f"{following.id} opens from this shot's last frame"
            if following.status is not ShotStatus.breakdown:
                line += f", and is already at {following.status.value}"
            found.append(line)
    if stage is Stage.frame:
        if shot_paths.approved_video.is_file():
            found.append(f"{shot.id}'s approved video was generated from this frame")
        crops = [
            paths.face_crop(scene_id, shot.id, char)
            for char in shot.characters
            if paths.face_crop(scene_id, shot.id, char).is_file()
        ]
        if crops:
            names = ", ".join(sorted(path.parent.parent.name for path in crops))
            found.append(f"identity passes read the face crops cut from it ({names})")
    return found


def plan_undo(project: Project, state: ProjectState, scene_id: str, shot_id: str) -> UndoPlan:
    """Work out what undoing would do. Changes nothing."""
    shot = project.shot(scene_id, shot_id)
    approved = _last_approved(shot.status)
    stage = approved.stage
    target = ShotStatus(stage.value)
    removes = [path for path in _artifacts(project.paths, scene_id, shot, stage) if path.is_file()]
    entry = next(
        (
            item
            for item in state.approvals(scene_id, shot_id)
            if item.action == "approve" and item.to_status == approved.value
        ),
        None,
    )
    return UndoPlan(
        stage=stage,
        target=target,
        removes=removes,
        dependents=_dependents(project, scene_id, shot, stage),
        approval=entry,
    )


def undo_approval(
    project: Project,
    state: ProjectState,
    scene_id: str,
    shot_id: str,
    *,
    note: str | None = None,
) -> UndoPlan:
    """Take the last approval back: remove what it wrote, return to its stage."""
    plan = plan_undo(project, state, scene_id, shot_id)
    shot = project.shot(scene_id, shot_id)
    removed: list[str] = []
    failed: dict[str, str] = {}
    for path in plan.removes:
        try:
            path.unlink()
        except OSError as exc:
            failed[project.paths.rel(path)] = str(exc)
        else:
            removed.append(project.paths.rel(path))
    if failed:
        raise OSError(
            "could not remove "
            + ", ".join(f"{name} ({reason})" for name, reason in failed.items())
        )

    previous = shot.status
    shot.status = plan.target
    project.save_shot(scene_id, shot)
    state.log_approval(
        ApprovalEntry(
            ts=_now(),
            scene=scene_id,
            shot=shot_id,
            action="unapprove",
            from_status=previous.value,
            to_status=plan.target.value,
            note=note,
            detail={"removed": removed, "dependents": plan.dependents},
            undoes=plan.approved_at,
        )
    )
    return plan
