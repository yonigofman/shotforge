"""Thin ffmpeg/ffprobe helpers: duration, last frame, rough cut.

Shotforge never ships a codec of its own; it calls the ffmpeg that is already
installed, and says so plainly when it is missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from shotforge.tools import ToolError, require_tool


def write_design_png(source: Path, target: Path) -> None:
    """Decode a downloaded still and store it as PNG, preserving original PNGs."""
    codec = {".png": "png", ".jpg": "mjpeg", ".jpeg": "mjpeg", ".webp": "webp"}[
        source.suffix.lower()
    ]
    command = [
        str(require_tool("ffmpeg")), "-nostdin", "-v", "error", "-xerror",
        "-f", "image2", "-pattern_type", "none", "-c:v", codec, "-i", str(source),
        "-frames:v", "1", "-threads", "1",
    ]
    if codec == "png":
        command += ["-f", "null", "-"]
    else:
        command += ["-update", "1", str(target)]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ToolError(f"ffmpeg could not process {source.name}: {exc}") from exc
    if result.returncode or (codec != "png" and not target.is_file()):
        raise ValueError(f"{source.name} is not a readable {source.suffix} image")
    if codec == "png":
        shutil.copyfile(source, target)


def probe_duration(video: Path) -> float | None:
    """Length of a video in seconds, or None when ffprobe cannot read it."""
    try:
        ffprobe = require_tool("ffprobe")
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
            timeout=30,
        )
    except (ToolError, OSError, subprocess.SubprocessError):
        return None
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
