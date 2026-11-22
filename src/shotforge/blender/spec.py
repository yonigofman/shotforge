"""Resolve the blockout adapter's YAML inputs before either Blender backend runs."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from hashlib import file_digest

from jinja2 import TemplateError
from pydantic import ValidationError

from shotforge.blender.errors import BlockoutError
from shotforge.models.blockout import BlockoutAxis, BlockoutSet, Point
from shotforge.models.common import SFModel, as_plain
from shotforge.models.scene import Scene, Seat
from shotforge.models.shot import Shot
from shotforge.project import Project
from shotforge.skills import find_skill, render_prompt
from shotforge.skills.registry import SkillError


class SetDescription(SFModel):
    set: BlockoutSet
    seating: list[Seat]


@dataclass(frozen=True)
class Mannequin:
    char: str
    position: Point
    heading: float


@dataclass(frozen=True)
class ShotCamera:
    name: str
    lens_mm: int
    location: tuple[float, float, float]
    target: tuple[float, float, float]
    shift_x: float


@dataclass(frozen=True)
class _CameraLayout:
    set: BlockoutSet
    mannequins: list[Mannequin]
    seats: dict[str, Point]
    axis: BlockoutAxis


def _describe_set(project: Project, scene: Scene) -> SetDescription:
    location = project.location(scene.location)
    try:
        skill = find_skill("blender-blockout", project.paths.skills)
        description = render_prompt(
            skill,
            {
                "location": as_plain(location),
                "scene": as_plain(scene),
                "default_set": as_plain(BlockoutSet()),
            },
        )
        return SetDescription.model_validate_json(description)
    except (SkillError, TemplateError, ValidationError) as exc:
        raise BlockoutError(f"blender-blockout skill: {exc}") from exc


def _window_position(set_: BlockoutSet) -> Point | None:
    if not (window := set_.window):
        return None
    width, depth, _ = set_.room
    return {
        "back": (window.offset, -depth / 2),
        "front": (window.offset, depth / 2),
        "left": (-width / 2, window.offset),
        "right": (width / 2, window.offset),
    }[window.wall]


def _mannequins(description: SetDescription) -> tuple[list[Mannequin], float]:
    set_ = description.set
    table = set_.hero_table
    x, y = table.center
    width, depth, _ = table.size
    row = y - depth / 2 - 0.29
    seating = description.seating
    labels = [seat.seat.rsplit(".", 1)[-1].lower() for seat in seating]
    named_offsets = {"left": 1, "center": 0, "right": -1}
    if len(set(labels)) == len(labels) and all(label in named_offsets for label in labels):
        offsets = [named_offsets[label] for label in labels]
    else:
        offsets = [1 - 2 * i / max(1, len(seating) - 1) for i in range(len(seating))]
        if len(seating) == 1:
            offsets = [0]
    positions = {
        seat.char: (x + offset * width / 3, row)
        for seat, offset in zip(seating, offsets, strict=True)
    }
    if len(positions) != len(seating):
        raise BlockoutError("scene seating assigns more than one seat to a character")

    figures = []
    for seat in seating:
        seat_x, seat_y = positions[seat.char]
        faces = seat.faces or "room"
        target = positions.get(faces) or {
            "table": (x, y),
            "window": _window_position(set_),
            "left": (seat_x - 1, seat_y),
            "right": (seat_x + 1, seat_y),
        }.get(faces.lower())
        heading = math.atan2(seat_y - target[1], seat_x - target[0]) + math.pi / 2 if target else 0
        figures.append(Mannequin(seat.char, (seat_x, seat_y), heading))
    return figures, row


def _axis(scene: Scene, set_: BlockoutSet, row: float) -> BlockoutAxis:
    axis = scene.axis_180
    if isinstance(axis, BlockoutAxis):
        return axis
    if axis and "room side" not in axis.lower() and "along the trio" not in axis.lower():
        raise BlockoutError(
            f"{scene.id}: use room side or provide axis_180.start, end and room_side as XY points"
        )
    x, y = set_.hero_table.center
    return BlockoutAxis(start=(x, row), end=(x + 1, row), room_side=(x, y))


def _camera_position(
    shot: Shot, set_: BlockoutSet, seats: dict[str, Point], subject: Point
) -> Point:
    position = shot.camera.position
    if isinstance(position, tuple):
        return position

    cue = (position or "room side").lower()
    table_x, table_y = set_.hero_table.center
    table_width, table_depth, _ = set_.hero_table.size
    x, y = subject[0], table_y + table_depth / 2 + 1.25
    if cue == "window end of table":
        window = _window_position(set_)
        direction = -1 if window and window[0] < table_x else 1
        x = table_x + direction * (table_width / 2 + 0.8)
        y -= 0.27
    elif cue.startswith("room side") and "shoulder" in cue:
        foreground = next(
            (item for item in shot.in_frame if item.role.startswith("foreground")), None
        )
        other_x = seats.get(foreground.char, subject)[0] if foreground else subject[0]
        direction = -1 if other_x < subject[0] else 1
        x = other_x + direction * table_width * 0.52
        y -= 0.27
    elif cue != "room side":
        raise BlockoutError(
            f"{shot.id}: unknown camera.position {position!r}; use an [x, y] position in metres"
        )
    width, depth, _ = set_.room
    return max(-0.45 * width, min(x, 0.45 * width)), min(y, 0.45 * depth)


def _subject_position(shot: Shot, layout: _CameraLayout) -> Point:
    subject = shot.subject
    center = layout.set.hero_table.center
    return layout.seats.get(subject.char, center) if subject else center


def _camera_layout(project: Project, scene: Scene) -> _CameraLayout:
    description = _describe_set(project, scene)
    mannequins, row = _mannequins(description)
    seats = {figure.char: figure.position for figure in mannequins}
    return _CameraLayout(description.set, mannequins, seats, _axis(scene, description.set, row))


def camera_axis_positions(
    project: Project, scene: Scene, shots: list[Shot]
) -> tuple[BlockoutAxis, dict[str, Point]]:
    """Resolve the same axis and camera positions that the blockout will render."""
    measured = {
        shot.id: point for shot in shots if isinstance(point := shot.camera.position, tuple)
    }
    if isinstance(scene.axis_180, BlockoutAxis) and len(measured) == len(shots):
        return scene.axis_180, measured
    layout = _camera_layout(project, scene)
    points = {
        shot.id: _camera_position(shot, layout.set, layout.seats, _subject_position(shot, layout))
        for shot in shots
    }
    return layout.axis, points


def _camera_level(
    shot: Shot, field: str, value: str | float | None, cues: dict[str, float], default: float
) -> float:
    if isinstance(value, str):
        try:
            return cues[value.lower()]
        except KeyError as exc:
            raise BlockoutError(f"{shot.id}: unknown camera.{field} {value!r}") from exc
    return default if value is None else value


def _camera(shot: Shot, layout: _CameraLayout) -> ShotCamera:
    camera = shot.camera
    name = camera.blender_cam
    if not name or "\0" in name or len(name.encode("utf-8")) > 63:
        raise BlockoutError(f"{shot.id}: camera.blender_cam needs a valid Blender object name")
    lens = 50 if camera.lens_mm is None else camera.lens_mm
    if lens < 1:
        raise BlockoutError(f"{shot.id}: camera.lens_mm must be at least 1")

    subject = shot.subject
    point = _subject_position(shot, layout)
    x, y = _camera_position(shot, layout.set, layout.seats, point)
    top = layout.set.hero_table.size[2]
    z = _camera_level(
        shot,
        "height",
        camera.height,
        {"eye level": top + 0.56, "table height": top, "low": top + 0.15, "high": top + 1.15},
        top + 0.45,
    )
    if z <= 0:
        raise BlockoutError(f"{shot.id}: camera.height must be above the floor")

    target_x, target_y = point
    target_z = top + 0.37
    distance = math.hypot(target_x - x, target_y - y)
    if distance <= 1e-6:
        raise BlockoutError(f"{shot.id}: camera.position must differ from its subject in XY")
    if camera.angle is not None:
        pitch = _camera_level(
            shot, "angle", camera.angle, {"eye level": 0, "high angle": -12, "low angle": 10}, 0
        )
        if not -90 < pitch < 90:
            raise BlockoutError(f"{shot.id}: camera.angle must be between -90 and 90 degrees")
        target_z = z + distance * math.tan(math.radians(pitch))
    shift = {"left_third": 1 / 6, "right_third": -1 / 6}.get(subject.frame_x, 0) if subject else 0
    return ShotCamera(name, lens, (x, y, z), (target_x, target_y, target_z), shift)


def _materials(project: Project, location: str, characters: set[str]) -> dict:
    textures = project.paths.textures
    materials = {}
    hashes = {}
    for surface, assignment in project.materials().root.items():
        kind, entity_id, part = surface.split(".")
        if (kind == "location" and entity_id != location) or (
            kind == "character" and entity_id not in characters
        ):
            continue
        path = textures / assignment.texture
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(textures):
            raise BlockoutError(f"{path}: texture must be inside the project's textures folder")
        if path not in hashes:
            with path.open("rb") as image:
                hashes[path] = file_digest(image, "sha256").hexdigest()
        key = part if kind == "location" else f"{entity_id}.{part}"
        materials[key] = {
            "texture": str(path),
            "uv_scale": assignment.uv_scale,
            "sha256": hashes[path],
        }
    return materials


def build_spec(project: Project, scene: Scene, shots: list[Shot]) -> dict:
    layout = _camera_layout(project, scene)
    cameras = {shot.id: _camera(shot, layout) for shot in shots}
    if len({camera.name for camera in cameras.values()}) != len(cameras):
        raise BlockoutError(f"{scene.id}: camera.blender_cam names must be unique in the scene")

    width, height = project.config.aspect_tuple
    if not width or not height:
        raise BlockoutError("project aspect_ratio must use two positive numbers")
    divisor = math.gcd(width, height)
    width, height = width // divisor, height // divisor
    scale = 960 // max(width, height)

    return {
        "scene": scene.id,
        "geometry": as_plain(layout.set),
        "mannequins": [asdict(figure) for figure in layout.mannequins],
        "cameras": {shot: asdict(camera) for shot, camera in cameras.items()},
        "axis": as_plain(layout.axis),
        "resolution": (max(4, width * scale), max(4, height * scale)),
        "materials": _materials(project, scene.location, set(layout.seats)),
    }
