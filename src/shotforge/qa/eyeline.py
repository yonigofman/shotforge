"""Review a dozen evenly spaced frames with the shared lens-contact skill."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from pydantic import Field, TypeAdapter
from yaml import YAMLError

from shotforge.checks import FAIL, PASS, WARN, Finding
from shotforge.engine import ClaudeCodeEngine, EngineError
from shotforge.models import Shot
from shotforge.models.common import SFModel
from shotforge.project import Project
from shotforge.skills import SkillError, find_skill
from shotforge.tools import ToolError, find_tool, require_tool
from shotforge.yamlio import ProjectFileError

SAMPLES = 12
CHECK = "looking into the lens"


class FrameReview(SFModel):
    frame: int = Field(strict=True, ge=1)
    status: Literal["clear", "lens_contact", "unclear"]
    evidence: str = Field(min_length=1)


def _sample_frames(video: Path, duration: float, target: Path) -> list[Path]:
    ffmpeg = require_tool("ffmpeg")
    subprocess.run(
        [
            str(ffmpeg), "-nostdin", "-v", "error", "-i", str(video),
            "-map", "0:v:0", "-vf",
            f"fps={SAMPLES / duration},scale='min(iw,960)':'min(ih,960)'"
            ":force_original_aspect_ratio=decrease",
            "-frames:v", str(SAMPLES), "-threads", "1", str(target / "frame_%02d.png"),
        ],
        capture_output=True, check=True, timeout=60,
    )
    return sorted(target.glob("frame_*.png"))


def check_eyeline(project: Project, shot: Shot, video: Path, duration: float) -> Finding:
    executable = find_tool("claude", overrides=project.config.tool_paths.model_dump())
    if executable is None:
        return Finding(
            CHECK, WARN, "Claude Code is unavailable for eyeline review",
            fix="install and sign in to Claude Code or set tool_paths.claude in project.yaml",
        )

    try:
        skill = find_skill("eyeline-qa", project.paths.skills)
        settings = project.claude_settings()
        engine = ClaudeCodeEngine(
            executable=executable, timeout_s=settings.timeout_s or 120, settings=settings,
        )
        with TemporaryDirectory(prefix="shotforge-eyeline-") as directory:
            root = Path(directory)
            frames = _sample_frames(video, duration, root)
            if not frames:
                return Finding(CHECK, WARN, "could not sample frames for eyeline review")
            inputs = {
                "in_frame": [
                    character.model_dump(exclude_none=True) for character in shot.in_frame
                ],
                "shot_summary": shot.summary,
                "technique": shot.technique,
                "frames": {index: path.name for index, path in enumerate(frames, 1)},
            }
            result = engine.run_skill(skill, inputs, cwd=root, files=frames)
    except (
        EngineError, ToolError, SkillError, ProjectFileError, OSError,
        subprocess.SubprocessError, YAMLError,
    ):
        return Finding(CHECK, WARN, "could not complete the eyeline review with Claude Code")

    if result.data.get("is_error") or result.data.get("permission_denials"):
        return Finding(CHECK, WARN, "Claude Code could not review the sampled frames")
    try:
        payload = result.data.get("parsed")
        if payload is None:
            payload = json.loads(result.text)
        reviews = TypeAdapter(list[FrameReview]).validate_python(payload["frames"])
    except (KeyError, TypeError, ValueError):
        return Finding(CHECK, WARN, "Claude Code returned an invalid eyeline review")

    if sorted(review.frame for review in reviews) != list(range(1, len(frames) + 1)):
        return Finding(CHECK, WARN, "Claude Code did not review every sampled frame")
    direct = sorted((r for r in reviews if r.status == "lens_contact"), key=lambda r: r.frame)
    if direct:
        numbers = ", ".join(str(review.frame) for review in direct)
        return Finding(
            CHECK, FAIL, f"unplanned lens contact in sample(s) {numbers}: {direct[0].evidence}",
            fix="regenerate with the subject looking at the eyeline anchor in the shot spec",
        )
    unclear = [r for r in reviews if r.status == "unclear"]
    if unclear:
        return Finding(CHECK, WARN, f"could not judge gaze in {len(unclear)}/{len(frames)} samples")
    return Finding(CHECK, PASS, f"no unplanned lens contact in {len(frames)} sampled frames")
