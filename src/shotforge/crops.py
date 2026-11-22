"""Cut face references from the head region of each blockout mask."""

from __future__ import annotations

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from shotforge.media import frame_size
from shotforge.models import Shot
from shotforge.models.common import ID_RE
from shotforge.paths import ProjectPaths
from shotforge.tools import ToolError, require_tool


def extract_face_crops(paths: ProjectPaths, scene_id: str, shot: Shot) -> dict[str, str]:
    """Refresh this shot's crops and return any skips for the approval card."""
    frame = paths.shot(scene_id, shot.id).approved_frame
    masks: dict[str, Path] = {}
    skipped: dict[str, str] = {}
    for char in shot.characters:
        if ID_RE.fullmatch(char):
            masks[char] = paths.mask(scene_id, shot.id, char)
        else:
            skipped[char] = "invalid character ID"
    try:
        size = frame_size(frame) if any(mask.is_file() for mask in masks.values()) else None
    except OSError:
        size = None
    for char, mask in masks.items():
        target = paths.face_crop(scene_id, shot.id, char)
        try:
            if not mask.is_file():
                raise ToolError(f"{mask.name} is missing")
            if size is None:
                raise ToolError(f"could not read {frame.name} with ffprobe")
            _crop(frame, size, mask, target)
        except (ToolError, OSError) as exc:
            # A crop from a previous approval no longer matches the approved frame.
            reason = str(exc)
            try:
                target.unlink(missing_ok=True)
            except OSError as remove_error:
                reason += f". Could not remove the previous crop ({remove_error})"
            skipped[char] = reason
    return skipped


def _crop(frame: Path, size: tuple[int, int], mask: Path, target: Path) -> None:
    ffmpeg = require_tool("ffmpeg")
    mask_size = frame_size(mask)
    if mask_size is None:
        raise ToolError(f"could not read {mask.name}")

    result = subprocess.run(
        [
            str(ffmpeg),
            "-v",
            "error",
            "-i",
            str(mask),
            "-vf",
            "format=gray,bbox=min_val=127,metadata=mode=print:file=-",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise ToolError(f"could not read {mask.name}")
    bounds = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if "lavfi.bbox.x1" not in bounds:
        raise ToolError(f"{mask.name} has no visible character pixels")

    left, right, top, bottom = (
        int(bounds[f"lavfi.bbox.{key}"]) for key in ("x1", "x2", "y1", "y2")
    )
    # ffmpeg reports inclusive bounds. Use the top third as the head region and
    # scale its edges to the frame size, rounding outward to keep small crops.
    mask_height_px = bottom - top + 1
    head_end = top + (mask_height_px + 2) // 3
    width, height = size
    mask_width, mask_height = mask_size
    x = left * width // mask_width
    y = top * height // mask_height
    x_end = ((right + 1) * width + mask_width - 1) // mask_width
    y_end = (head_end * height + mask_height - 1) // mask_height

    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=target.parent) as tmp:
        output = Path(tmp) / target.name
        result = subprocess.run(
            [
                str(ffmpeg),
                "-v",
                "error",
                "-i",
                str(frame),
                "-vf",
                f"crop={x_end - x}:{y_end - y}:{x}:{y}:exact=1",
                "-frames:v",
                "1",
                "-update",
                "1",
                str(output),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode or not output.is_file():
            raise ToolError(f"could not crop {frame.name}")
        output.replace(target)
