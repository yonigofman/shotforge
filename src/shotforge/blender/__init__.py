"""Run the per-character mask pass against an existing Blender blockout."""

from __future__ import annotations

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from shotforge.models.common import ID_RE
from shotforge.project import Project
from shotforge.tools import ToolError, require_tool


def render_masks(project: Project, scene_id: str, shot_id: str) -> list[Path]:
    shot = project.shot(scene_id, shot_id)
    characters = list(dict.fromkeys(shot.characters))
    if not characters:
        return []
    for character_id in characters:
        if not ID_RE.fullmatch(character_id):
            raise ValueError(f"Invalid character id {character_id!r}")

    blend_file = project.paths.blend_file(scene_id)
    if not blend_file.is_file():
        raise ToolError(f"Create the Blender blockout at {project.paths.rel(blend_file)} first")

    blender = require_tool("blender")
    script = Path(__file__).parent / "scripts" / "masks.py"
    renders = project.paths.renders(scene_id, shot_id)
    renders.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=renders) as tmp:
        command = [
            str(blender),
            "--background",
            str(blend_file),
            "--python-exit-code",
            "1",
            "--python",
            str(script),
            "--",
            shot.camera.blender_cam or "",
            tmp,
            *characters,
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, errors="replace")
        except OSError as exc:
            raise ToolError(f"Could not start Blender ({exc})") from exc
        if result.returncode:
            message = result.stderr.strip() or result.stdout.strip()
            raise ToolError(f"Blender could not render masks for {shot_id}\n{message}")

        targets = [project.paths.mask(scene_id, shot_id, char) for char in characters]
        outputs = [Path(tmp) / target.name for target in targets]
        if not all(path.is_file() for path in outputs):
            raise ToolError(f"Blender did not finish writing masks for {shot_id}")
        for output, target in zip(outputs, targets, strict=True):
            output.replace(target)
    return targets
