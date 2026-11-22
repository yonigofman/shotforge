"""Media helpers for video probing and still images.

Video operations use the installed FFmpeg tools. Still dimensions use Pillow.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from math import isfinite
from pathlib import Path

from PIL import Image

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
    """Read a video duration, falling back to FFmpeg when ffprobe fails."""
    commands = (
        ("ffprobe", ["-v", "error", "-show_entries", "format=duration", "-of", "json", str(video)],
         _ffprobe_duration),
        ("ffmpeg", [
            "-nostdin", "-hide_banner", "-v", "info", "-i", str(video),
            "-map", "0:v:0", "-c", "copy", "-t", "0", "-f", "null", "-",
        ], _ffmpeg_duration),
    )
    errors = []
    for name, arguments, parse in commands:
        try:
            executable = require_tool(name)
            result = subprocess.run(
                [str(executable), *arguments], capture_output=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
        except ToolError as exc:
            errors.append(str(exc))
            continue
        except (OSError, subprocess.SubprocessError) as exc:
            errors.append(f"Could not run {name}: {exc}")
            continue
        if result.returncode == 0:
            duration = parse(result)
            if duration is not None and isfinite(duration) and duration > 0:
                return duration
    if len(errors) == len(commands):
        raise ToolError(" ".join(errors))
    return None


def _ffprobe_duration(result: subprocess.CompletedProcess[str]) -> float | None:
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


def _ffmpeg_duration(result: subprocess.CompletedProcess[str]) -> float | None:
    match = re.search(r"^\s*Duration: (\d+):(\d\d):(\d\d(?:\.\d+)?),", result.stderr, re.M)
    if match is None:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


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
    """Return an image's pixel dimensions if Pillow can read them."""
    try:
        with Image.open(image) as still:
            return still.size
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
