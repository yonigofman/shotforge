"""Thin ffmpeg/ffprobe helpers: duration, last frame, rough cut.

Shotforge never ships a codec of its own; it calls the ffmpeg that is already
installed, and says so plainly when it is missing.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from shotforge.tools import ToolError, require_tool


def probe_duration(video: Path) -> float | None:
    """Length of a video in seconds, or None when ffprobe cannot read it."""
    try:
        ffprobe = require_tool("ffprobe")
    except ToolError:
        return None
    result = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(video),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return None


def extract_last_frame(video: Path, target: Path) -> Path:
    """Pull the final frame, which feeds the next shot as an expression reference."""
    ffmpeg = require_tool("ffmpeg")
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-sseof",
            "-0.2",
            "-i",
            str(video),
            "-update",
            "1",
            "-frames:v",
            "1",
            str(target),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not target.is_file():
        raise ToolError(f"ffmpeg could not read the last frame of {video.name}:\n{result.stderr}")
    return target


def frame_size(image: Path) -> tuple[int, int] | None:
    """Pixel size of an image, via ffprobe (no image library needed)."""
    try:
        ffprobe = require_tool("ffprobe")
    except ToolError:
        return None
    result = subprocess.run(
        [
            str(ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(image),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        stream = json.loads(result.stdout)["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (KeyError, IndexError, ValueError, json.JSONDecodeError):
        return None
