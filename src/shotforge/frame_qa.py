"""Review a first-frame take against the shot spec and its visual references."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from shotforge.checks import FAIL, PASS, WARN, Finding
from shotforge.engine import EngineCancelled, EngineError, EngineProgress, EngineResult, get_engine
from shotforge.models import Shot
from shotforge.models.common import as_plain
from shotforge.pipeline import face_crop_refs
from shotforge.project import Project, ProjectError
from shotforge.skills import find_skill
from shotforge.skills.registry import SkillError
from shotforge.tools import ToolError
from shotforge.yamlio import ProjectFileError

ROWS = (
    "identity",
    "eyeline",
    "eye level",
    "seating and 180° side",
    "size and perspective",
    "strangers, text and neon",
    "set and windows",
    "prop continuity",
    "gestures and hands",
    "AI look",
)


def review(
    project: Project, scene_id: str, shot: Shot, frame: Path, *,
    progress: EngineProgress | None = None,
) -> list[Finding]:
    """Return advisory findings, or a warning if the vision run is unavailable."""
    try:
        engine = get_engine(project.config.engine)
        skill = find_skill("frame-qa", project.paths.skills)
        inputs, files = _inputs(project, scene_id, shot, frame)
        if progress:
            progress.status("Waiting for Claude to review the frame…")
            result = engine.run_skill_with_progress(
                skill, inputs, cwd=project.paths.root, files=files, progress=progress,
            )
        else:
            result = engine.run_skill(skill, inputs, cwd=project.paths.root, files=files)
        return _findings(result)
    except EngineCancelled:
        raise
    except (
        EngineError, ToolError, SkillError, ProjectError, ProjectFileError, OSError, yaml.YAMLError
    ) as exc:
        return [Finding("vision", WARN, f"Frame vision QA could not run: {exc}")]


def _inputs(
    project: Project, scene_id: str, shot: Shot, frame: Path
) -> tuple[dict[str, Any], list[Path]]:
    paths = project.paths
    scene = project.scene(scene_id)
    previous = project.previous_shot(scene_id, shot.id)
    visible = [framed.char for framed in shot.in_frame if framed.role != "offscreen"]
    crops = face_crop_refs(project, (scene_id, shot.id), visible)
    files = [frame]

    def reference(path: Path | None) -> str | None:
        if path is None or not path.is_file():
            return None
        files.append(path)
        return str(path)

    inputs = {
        "required_checks": list(ROWS),
        "images": {
            "take": str(frame),
            "face_crops": {character: reference(path) for character, path in crops.items()},
            "blockout": reference(paths.blockout(scene_id, shot.id)),
            "wall_ref": reference(paths.wall_ref(scene.location)),
            "previous_last_frame": reference(paths.shot(scene_id, previous.id).last_frame)
            if previous else None,
        },
        "scene": as_plain(scene),
        "shot": as_plain(shot),
        "location": as_plain(project.location(scene.location))
        if paths.entity_file("location", scene.location).is_file() else None,
        "style": as_plain(project.style()) if paths.style.is_file() else None,
        "previous_continuity_out": previous.continuity.out if previous else None,
    }
    return inputs, files


def _findings(result: EngineResult) -> list[Finding]:
    if result.data.get("is_error"):
        raise EngineError(f"Claude Code reported an error: {result.text[:500]}")
    if result.data.get("permission_denials"):
        raise EngineError("Claude Code was denied access to a review image")
    payload = result.data.get("parsed")
    if payload is None:
        try:
            payload = json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise EngineError("the model did not return a JSON report") from exc
    if isinstance(payload, dict) and isinstance(error := payload.get("error"), str):
        raise EngineError(error[:500])
    if not isinstance(payload, dict) or not isinstance(payload.get("findings"), list):
        raise EngineError("the model report did not include a findings list")

    by_check = defaultdict(list)
    for row in payload["findings"]:
        if isinstance(row, dict) and isinstance(row.get("check"), str):
            by_check[row["check"]].append(row)

    findings = []
    for check in ROWS:
        rows = by_check[check]
        if len(rows) == 1:
            row = rows[0]
            level, message, fix = row.get("level"), row.get("message"), row.get("fix")
            target, region = row.get("target"), row.get("region")
            if (
                level in (PASS, WARN, FAIL)
                and isinstance(message, str) and message.strip()
                and (fix is None or isinstance(fix, str))
            ):
                findings.append(
                    Finding(
                        check, level, message.strip(), fix,
                        target=target.strip() or None if isinstance(target, str) else None,
                        region=region.strip() or None if isinstance(region, str) else None,
                    )
                )
                continue
        findings.append(Finding(check, WARN, "The model did not return a usable verdict."))
    return findings
