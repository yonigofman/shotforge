"""What the cockpit and the CLI both do: the few operations on a shot.

One place, so the web screens and the terminal cannot drift apart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from shotforge import checks
from shotforge.crops import extract_face_crops
from shotforge.media import extract_last_frame
from shotforge.models import Shot, ShotStatus
from shotforge.pipeline import Action, TransitionError, approve, begin_next_stage, next_action
from shotforge.project import Project
from shotforge.skills import find_skill, render_prompt, save_prompt, shot_context
from shotforge.skills.registry import Skill
from shotforge.state import ProjectState
from shotforge.takes import TakeKind, approve_take, get_take, list_takes
from shotforge.tools import ToolError


@dataclass
class PromptResult:
    skill: Skill
    text: str
    path: Path
    action: Action


def build_prompt(
    project: Project, state: ProjectState, scene_id: str, shot_id: str
) -> PromptResult:
    """Render the prompt for whatever this shot needs now, and keep a copy."""
    action = next_action(project, state, scene_id, shot_id)
    if not action.skill:
        raise ValueError(f"{shot_id} is at a step with no prompt ({action.title})")
    skill = find_skill(action.skill, project.paths.skills)
    frame_pass = state.shot(scene_id, shot_id).frame_pass
    context = shot_context(
        project,
        scene_id,
        shot_id,
        attachments=action.attachments,
        extra={"frame_pass": frame_pass, "tool": action.tool},
    )
    text = render_prompt(skill, context)
    path = save_prompt(project, scene_id, shot_id, skill, text)
    return PromptResult(skill=skill, text=text, path=path, action=action)


def run_qa(
    project: Project, scene_id: str, shot_id: str, kind: TakeKind, number: int,
    *, vision: bool = True,
) -> list[checks.Finding]:
    """Check one take and write its report alongside the shot spec."""
    shot = project.shot(scene_id, shot_id)
    shot_paths = project.paths.shot(scene_id, shot_id)
    take = get_take(shot_paths, kind, number)
    findings = checks.check_spec(project, scene_id, shot)
    if kind is TakeKind.frame:
        findings += checks.check_frame_file(project, take.path)
    else:
        findings += checks.check_video_file(project, scene_id, shot, take.path, vision=vision)
    _write_report(take.qa_report(shot_paths), findings)
    return findings


def read_qa(path: Path) -> dict | None:
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        isinstance(report, dict)
        and report.get("verdict") in (checks.PASS, checks.WARN, checks.FAIL)
        and isinstance(report.get("findings"), list)
    ):
        return report
    return None


def _write_report(path: Path, findings: list[checks.Finding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "verdict": checks.summarise(findings),
        "findings": [
            {"check": f.check, "level": f.level, "message": f.message, "fix": f.fix}
            for f in findings
        ],
    }
    with TemporaryDirectory(prefix=".qa-", dir=path.parent) as directory:
        draft = Path(directory) / path.name
        draft.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        draft.replace(path)


def approve_frame(
    project: Project,
    state: ProjectState,
    scene_id: str,
    shot_id: str,
    number: int,
    note: str | None = None,
) -> Shot:
    """Approve a frame take and extract the face references for later shots."""
    shot = project.shot(scene_id, shot_id)
    if shot.status is not ShotStatus.frame:
        raise TransitionError(
            f"{shot_id} is at {shot.status.value}, not waiting for a frame approval"
        )
    shot_paths = project.paths.shot(scene_id, shot_id)
    approved = approve_take(shot_paths, TakeKind.frame, number)
    skipped = extract_face_crops(project.paths, scene_id, shot)
    return approve(
        project,
        state,
        scene_id,
        shot_id,
        note=note,
        detail={
            "take": number,
            "approved": project.paths.rel(approved),
            "face_crop_skips": skipped,
        },
    )


def approve_video(
    project: Project,
    state: ProjectState,
    scene_id: str,
    shot_id: str,
    number: int,
    note: str | None = None,
) -> tuple[Shot, Path | None]:
    """Approve a video take, then pull its last frame for the next shot."""
    shot_paths = project.paths.shot(scene_id, shot_id)
    approved = approve_take(shot_paths, TakeKind.video, number)
    last_frame: Path | None = None
    try:
        last_frame = extract_last_frame(approved, shot_paths.last_frame)
    except ToolError:
        last_frame = None
    shot = approve(
        project,
        state,
        scene_id,
        shot_id,
        note=note,
        detail={
            "take": number,
            "approved": project.paths.rel(approved),
            "last_frame": project.paths.rel(last_frame) if last_frame else None,
        },
    )
    return shot, last_frame


def advance_frame_pass(project: Project, state: ProjectState, scene_id: str, shot_id: str) -> int:
    """Move to the identity pass. It edits the latest take, inside the frame stage.

    Approving ends the frame stage, so pass 2 only exists before the gate: the
    composition is locked by the take it works on, not by an approval.
    """
    shot_paths = project.paths.shot(scene_id, shot_id)
    if not list_takes(shot_paths, TakeKind.frame):
        raise ValueError("pass 2 edits a pass 1 frame; file one from the inbox first")
    state.update_shot(scene_id, shot_id, frame_pass=2)
    return 2


def stage_label(shot: Shot) -> str:
    from shotforge.pipeline import STAGE_LABEL

    label = STAGE_LABEL[shot.status.stage]
    if shot.status is ShotStatus.done:
        return label
    return f"{label} — approved" if shot.status.is_approved else label


def begin(project: Project, state: ProjectState, scene_id: str, shot_id: str) -> Shot:
    shot = begin_next_stage(project, state, scene_id, shot_id)
    if shot.status is ShotStatus.frame:
        state.update_shot(scene_id, shot_id, frame_pass=1)
    return shot
