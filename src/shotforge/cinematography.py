"""Propose a camera during Breakdown, then apply the reviewed changes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator

from shotforge import checks
from shotforge.engine import EngineError, get_engine
from shotforge.models import Scene, Shot, ShotStatus
from shotforge.models.common import ID_RE, SFModel, as_plain
from shotforge.models.shot import Technique
from shotforge.project import Project, ProjectError
from shotforge.skills import find_skill
from shotforge.skills.registry import SkillError, bundled_root
from shotforge.tools import ToolError
from shotforge.yamlio import render_yaml

SKILL_NAME = "cinematography"
Reason = Annotated[str, Field(min_length=1, pattern=r"^[^\r\n]+$")]
Height = (
    Literal["eye level", "table height", "low", "high"]
    | Annotated[float, Field(gt=0, allow_inf_nan=False)]
)
Angle = (
    Literal["eye level", "high angle", "low angle"]
    | Annotated[float, Field(gt=-90, lt=90, allow_inf_nan=False)]
)


class CameraError(Exception):
    """The camera proposal could not be generated, verified or saved."""


class TechniqueChoice(SFModel):
    name: Technique
    why: Reason


class CameraPlan(SFModel):
    lens_mm: Annotated[int, Field(gt=0, strict=True)]
    lens_why: Reason
    height: Height
    height_why: Reason
    angle: Angle
    angle_why: Reason
    techniques: list[TechniqueChoice]

    @field_validator("techniques")
    @classmethod
    def _unique_techniques(cls, choices: list[TechniqueChoice]) -> list[TechniqueChoice]:
        if len({choice.name for choice in choices}) != len(choices):
            raise ValueError("each technique can be chosen only once")
        return choices


@dataclass(frozen=True)
class CameraProposal:
    basis: str
    plan: CameraPlan


@dataclass
class _Context:
    scene: Scene
    shot: Shot
    previous: Shot | None
    following: Shot | None

    def inputs(self) -> dict:
        return as_plain(
            {
                "scene": self.scene,
                "shot": self.shot,
                "previous_shot": self.previous,
                "next_shot": self.following,
            }
        )

    def fingerprint(self) -> str:
        payload = json.dumps(self.inputs(), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _context(project: Project, scene_id: str, shot_id: str) -> _Context:
    if not (ID_RE.fullmatch(scene_id) and ID_RE.fullmatch(shot_id)):
        raise ProjectError("Use scene and shot IDs with lowercase letters, digits and underscores.")
    shot = project.shot(scene_id, shot_id)
    if shot.status is not ShotStatus.breakdown:
        raise CameraError(f"Open {shot_id} in Breakdown to change its camera.")
    scene = project.scene(scene_id)
    if scene.id != scene_id or shot.id != shot_id:
        raise CameraError("The scene and shot IDs in YAML must match their folder names.")
    return _Context(
        scene,
        shot,
        project.previous_shot(scene_id, shot_id),
        project.next_shot(scene_id, shot_id),
    )


def _candidate(project: Project, context: _Context, plan: CameraPlan) -> Shot:
    shot = context.shot.model_copy(deep=True)
    shot.camera.lens_mm = plan.lens_mm
    shot.camera.height = plan.height
    shot.camera.angle = plan.angle
    shot.camera.why = " ".join((plan.lens_why, plan.height_why, plan.angle_why))
    shot.technique = [choice.name.value for choice in plan.techniques]
    shot.technique_why = " ".join(choice.why for choice in plan.techniques) or None
    findings = checks.check_camera(
        project, context.scene, shot, context.previous, following=context.following, proposal=True
    )
    failures = [finding for finding in findings if finding.failed]
    if failures:
        detail = "\n".join(
            f"{finding.message}\nFix — {finding.fix}" if finding.fix else finding.message
            for finding in failures
        )
        raise CameraError(f"Camera proposal refused.\n{detail}")
    return shot


def propose_camera(project: Project, scene_id: str, shot_id: str) -> tuple[CameraProposal, str]:
    """Generate and validate a proposal, then show the diff without writing the shot."""
    context = _context(project, scene_id, shot_id)
    try:
        skill = find_skill(SKILL_NAME, project.paths.skills)
        reference = Path("references/techniques.md")
        library = skill.root / reference
        if not library.is_file():
            library = bundled_root() / SKILL_NAME / reference
        inputs = {**context.inputs(), "technique_library": library.read_text(encoding="utf-8")}
        engine = get_engine(project.config.engine)
        result = engine.run_skill(skill, inputs, cwd=project.paths.root)
        plan = CameraPlan.model_validate(result.structured())
    except ValidationError as exc:
        details = ", ".join(
            f"{'.'.join(map(str, err['loc']))} ({err['msg']})"
            for err in exc.errors(include_input=False, include_url=False)
        )
        raise CameraError(
            f"The cinematography skill returned an invalid camera plan. {details}"
        ) from exc
    except json.JSONDecodeError as exc:
        raise CameraError("The cinematography skill did not return a JSON camera plan.") from exc
    except (EngineError, SkillError, ToolError, OSError, UnicodeError) as exc:
        raise CameraError(str(exc)) from exc
    candidate = _candidate(project, context, plan)
    label = project.paths.rel(project.paths.shot(scene_id, shot_id).spec)
    diff = "".join(
        unified_diff(
            render_yaml(context.shot).splitlines(keepends=True),
            render_yaml(candidate).splitlines(keepends=True),
            fromfile=f"{label} (current)",
            tofile=f"{label} (proposed)",
        )
    )
    return CameraProposal(basis=context.fingerprint(), plan=plan), diff


def apply_camera(project: Project, scene_id: str, shot_id: str, proposal: CameraProposal) -> None:
    """Apply an explicitly confirmed proposal if its shot and neighbours still match."""
    context = _context(project, scene_id, shot_id)
    if context.fingerprint() != proposal.basis:
        raise CameraError("The scene or shot specs changed. Generate a new camera proposal.")
    candidate = _candidate(project, context, proposal.plan)
    if candidate != context.shot:
        project.save_shot(scene_id, candidate)
