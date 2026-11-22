"""The per-shot state machine, and the one question the shot card answers:
what needs doing in this shot now, and in which tool?

Every stage closes on your approval. A transition without an approval is
refused, and each approval is written to the log with an optional note.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path

from shotforge.models import Shot, ShotStatus, Stage
from shotforge.project import Project
from shotforge.state import ApprovalEntry, ProjectState
from shotforge.takes import TakeKind, approved_path, list_takes

#: The statuses in order. A shot only ever moves one step at a time.
ORDER: tuple[ShotStatus, ...] = (
    ShotStatus.breakdown,
    ShotStatus.breakdown_approved,
    ShotStatus.blockout,
    ShotStatus.blockout_approved,
    ShotStatus.frame,
    ShotStatus.frame_approved,
    ShotStatus.video,
    ShotStatus.video_approved,
    ShotStatus.done,
)

#: Transitions that need your approval, and what the gate is called.
GATES: dict[ShotStatus, str] = {
    ShotStatus.breakdown: "Approve the shot list and beats",
    ShotStatus.blockout: "Approve the blockout",
    ShotStatus.frame: "Approve the first frame",
    ShotStatus.video: "Approve the video",
}

STAGE_LABEL = {
    Stage.breakdown: "Breakdown",
    Stage.blockout: "Blockout",
    Stage.frame: "Frames",
    Stage.video: "Video",
    Stage.done: "Done",
}


class TransitionError(Exception):
    """A move the state machine does not allow."""


@dataclass
class Attachment:
    """A file to attach to a prompt, in the order the tool expects it."""

    order: int
    label: str
    path: Path
    exists: bool

    @property
    def name(self) -> str:
        return self.path.name


@dataclass
class Action:
    """What the shot card shows: the next thing to do, and what it needs."""

    kind: str  # work | gate | begin | finished
    title: str
    tool: str
    detail: str = ""
    skill: str | None = None
    gate: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.blocked_by


def next_status(status: ShotStatus) -> ShotStatus | None:
    index = ORDER.index(status)
    return ORDER[index + 1] if index + 1 < len(ORDER) else None


def needs_approval(status: ShotStatus) -> bool:
    return status in GATES


def approve(
    project: Project,
    state: ProjectState,
    scene_id: str,
    shot_id: str,
    *,
    note: str | None = None,
    detail: dict | None = None,
) -> Shot:
    """Close the current stage. Only this function sets an ``*_approved`` status."""
    shot = project.shot(scene_id, shot_id)
    if not needs_approval(shot.status):
        raise TransitionError(
            f"{shot_id} is at {shot.status.value}, which is not waiting for an approval"
        )
    target = next_status(shot.status)
    assert target is not None
    shot.status = target
    project.save_shot(scene_id, shot)
    state.log_approval(
        ApprovalEntry(
            ts=_now(),
            scene=scene_id,
            shot=shot_id,
            action="approve",
            from_status=ORDER[ORDER.index(target) - 1].value,
            to_status=target.value,
            note=note,
            detail=detail or {},
        )
    )
    return shot


def begin_next_stage(
    project: Project, state: ProjectState, scene_id: str, shot_id: str
) -> Shot:
    """Open the next stage after an approval (logged, but no gate of its own)."""
    shot = project.shot(scene_id, shot_id)
    if not shot.status.is_approved or shot.status is ShotStatus.done:
        raise TransitionError(f"{shot_id} is still working at {shot.status.value}")
    target = next_status(shot.status)
    if target is None:
        raise TransitionError(f"{shot_id} is already done")
    previous = shot.status
    shot.status = target
    project.save_shot(scene_id, shot)
    state.log_approval(
        ApprovalEntry(
            ts=_now(),
            scene=scene_id,
            shot=shot_id,
            action="begin",
            from_status=previous.value,
            to_status=target.value,
        )
    )
    return shot


def reopen(
    project: Project,
    state: ProjectState,
    scene_id: str,
    shot_id: str,
    stage: Stage,
    *,
    note: str | None = None,
) -> Shot:
    """Go back to a stage — logged, because later work may rest on it."""
    shot = project.shot(scene_id, shot_id)
    target = ShotStatus(stage.value)
    if ORDER.index(target) > ORDER.index(shot.status):
        raise TransitionError(
            f"{stage.value} is ahead of {shot.status.value}; use the approval gate instead"
        )
    previous = shot.status
    shot.status = target
    project.save_shot(scene_id, shot)
    state.log_approval(
        ApprovalEntry(
            ts=_now(),
            scene=scene_id,
            shot=shot_id,
            action="reopen",
            from_status=previous.value,
            to_status=target.value,
            note=note,
        )
    )
    return shot


def _now() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def _attachment(order: int, label: str, path: Path) -> Attachment:
    return Attachment(order=order, label=label, path=path, exists=path.is_file())


def frame_attachments(
    project: Project, scene_id: str, shot_id: str, frame_pass: int
) -> list[Attachment]:
    """The files for a frame pass, numbered in the order ChatGPT should receive them."""
    shot = project.shot(scene_id, shot_id)
    paths = project.paths
    shot_paths = paths.shot(scene_id, shot_id)
    items: list[Attachment] = []
    order = 1
    if frame_pass == 1:
        items.append(_attachment(order, "Blender blockout", paths.blockout(scene_id, shot_id)))
        order += 1
        for framed in shot.in_frame:
            items.append(
                _attachment(order, f"{framed.char} sheet", paths.sheet("character", framed.char))
            )
            order += 1
        location = project.scene(scene_id).location
        wall_ref = paths.design_dir("location", location) / "wall_ref.png"
        items.append(_attachment(order, f"{location} wall reference", wall_ref))
        order += 1
    else:
        takes = list_takes(shot_paths, TakeKind.frame)
        latest = takes[-1].path if takes else approved_path(shot_paths, TakeKind.frame)
        items.append(_attachment(order, "Frame from pass 1", latest))
        order += 1
        for framed in shot.in_frame:
            crops = paths.face_crops(framed.char)
            found = sorted(crops.glob("*.png")) if crops.is_dir() else []
            items.append(
                _attachment(
                    order, f"{framed.char} face crop", found[-1] if found else crops / "latest.png"
                )
            )
            order += 1
            items.append(
                _attachment(
                    order, f"{framed.char} mask", paths.mask(scene_id, shot_id, framed.char)
                )
            )
            order += 1
    previous = project.previous_shot(scene_id, shot_id)
    if previous is not None:
        items.append(
            _attachment(
                order,
                f"last frame of {previous.id}",
                paths.shot(scene_id, previous.id).last_frame,
            )
        )
    return items


def video_attachments(project: Project, scene_id: str, shot_id: str) -> list[Attachment]:
    shot_paths = project.paths.shot(scene_id, shot_id)
    items = [_attachment(1, "Approved first frame", approved_path(shot_paths, TakeKind.frame))]
    order = 2
    for character_id in project.shot(scene_id, shot_id).characters:
        ref = project.paths.ref("character", character_id)
        items.append(_attachment(order, f"{character_id} clean ref (@tag)", ref))
        order += 1
    return items


def next_action(project: Project, state: ProjectState, scene_id: str, shot_id: str) -> Action:
    """What to do now in this shot — the shot card's whole content."""
    shot = project.shot(scene_id, shot_id)
    shot_paths = project.paths.shot(scene_id, shot_id)
    status = shot.status

    if status is ShotStatus.done:
        return Action(kind="finished", title="Shot is done", tool="—")

    if status.is_approved:
        target = next_status(status)
        assert target is not None
        title = (
            "Mark the shot done"
            if target is ShotStatus.done
            else f"Start {STAGE_LABEL[target.stage]}"
        )
        return Action(
            kind="begin",
            title=title,
            tool="Shotforge",
            detail=f"{STAGE_LABEL[status.stage]} is approved.",
        )

    if status is ShotStatus.breakdown:
        return Action(
            kind="work",
            title="Review the beats and the camera",
            tool="Shotforge",
            detail="Check the beats, the dialogue and who is in frame, then approve.",
            gate=GATES[status],
        )

    if status is ShotStatus.blockout:
        blockout = project.paths.blockout(scene_id, shot_id)
        return Action(
            kind="work",
            title="Render the blockout",
            tool="Blender",
            detail=(
                f"Render camera {shot.camera.blender_cam or '(set camera)'} "
                f"to {project.paths.rel(blockout)}."
            ),
            gate=GATES[status],
            attachments=[_attachment(1, "Blockout render", blockout)],
            blocked_by=(
                []
                if blockout.is_file()
                else [f"{project.paths.rel(blockout)} is not rendered yet"]
            ),
        )

    if status is ShotStatus.frame:
        frame_pass = state.shot(scene_id, shot_id).frame_pass
        skill = "chatgpt-first-frame" if frame_pass == 1 else "chatgpt-identity-pass"
        title = (
            "Pass 1 — ChatGPT, new chat"
            if frame_pass == 1
            else "Pass 2 — ChatGPT, identity pass"
        )
        detail = (
            "Lock the composition from the blockout. Download the result to _inbox."
            if frame_pass == 1
            else "Fix identity against the face crops. Mark the area shown in the mask."
        )
        takes = list_takes(shot_paths, TakeKind.frame)
        return Action(
            kind="work",
            title=title,
            tool="ChatGPT",
            detail=detail,
            skill=skill,
            gate=GATES[status],
            attachments=frame_attachments(project, scene_id, shot_id, frame_pass),
            blocked_by=(
                []
                if takes
                else ["no frame take yet — generate one and drop it on the card"]
            ),
        )

    if status is ShotStatus.video:
        takes = list_takes(shot_paths, TakeKind.video)
        return Action(
            kind="work",
            title="Generate the video",
            tool="Grok",
            detail="Static camera, beats with timecodes. Download the result to _inbox.",
            skill="grok-video",
            gate=GATES[status],
            attachments=video_attachments(project, scene_id, shot_id),
            blocked_by=(
                []
                if takes
                else ["no video take yet — generate one and drop it on the card"]
            ),
        )

    raise TransitionError(f"no action defined for {status.value}")
