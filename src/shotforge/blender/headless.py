"""Run the same render job in a standalone background Blender process."""

from __future__ import annotations

import subprocess
from pathlib import Path

from shotforge.blender.errors import BlockoutError
from shotforge.models.project import ToolPaths
from shotforge.tools import ToolError, require_tool


def render(script: Path, job: Path, timeout: float, tool_paths: ToolPaths) -> None:
    try:
        blender = require_tool("blender", overrides=tool_paths.model_dump()).resolve()
        result = subprocess.run(
            [
                str(blender), "--background", "--factory-startup", "--python-exit-code", "1",
                "--python", str(script), "--", str(job),
            ],
            capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
            check=False,
        )
    except (ToolError, OSError, subprocess.TimeoutExpired) as exc:
        raise BlockoutError(str(exc)) from exc
    if result.returncode != 0:
        detail = (result.stderr.strip() or result.stdout.strip())[-1500:]
        raise BlockoutError(detail or f"Blender exited with status {result.returncode}")
