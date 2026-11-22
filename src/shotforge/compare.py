"""What a take can be held against.

Judging a frame is never done on its own: it is held against the frame it
has to cut from, the sheet of the person in it, the blockout it was composed
on, or the take before it. This gathers those, so the compare view has a
list to offer rather than two pictures someone happened to open.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shotforge.project import Project
from shotforge.takes import TakeKind, approved_path, list_takes


@dataclass(frozen=True)
class Reference:
    """One image worth comparing against, and what to call it."""

    key: str
    label: str
    path: Path
    group: str

    @property
    def exists(self) -> bool:
        return self.path.is_file()


def references(project: Project, scene_id: str, shot_id: str) -> list[Reference]:
    """Everything in this shot's reach, in the order it is usually wanted."""
    paths = project.paths
    shot = project.shot(scene_id, shot_id)
    shot_paths = paths.shot(scene_id, shot_id)
    found: list[Reference] = []

    for take in list_takes(shot_paths, TakeKind.frame):
        found.append(
            Reference(f"take:{take.number}", f"Take v{take.number}", take.path, "This shot")
        )
    approved = approved_path(shot_paths, TakeKind.frame)
    if approved.is_file():
        found.append(Reference("approved", "Approved frame", approved, "This shot"))
    found.append(Reference("blockout", "Blockout", paths.blockout(scene_id, shot_id), "This shot"))

    previous = project.previous_shot(scene_id, shot_id)
    if previous is not None:
        previous_paths = paths.shot(scene_id, previous.id)
        found.append(
            Reference(
                "previous_last",
                f"{previous.id} last frame",
                previous_paths.last_frame,
                "The cut in",
            )
        )
        found.append(
            Reference(
                "previous_frame",
                f"{previous.id} first frame",
                approved_path(previous_paths, TakeKind.frame),
                "The cut in",
            )
        )

    for character in shot.characters:
        found.append(
            Reference(
                f"sheet:{character}",
                f"{character} sheet",
                paths.sheet("character", character),
                "Identity",
            )
        )
        crop = paths.face_crop(scene_id, shot_id, character)
        found.append(Reference(f"crop:{character}", f"{character} face crop", crop, "Identity"))

    return [reference for reference in found if reference.exists]


def pick(found: list[Reference], key: str | None, fallback: int) -> Reference | None:
    """The reference a key names, or the one at ``fallback`` when it names none."""
    if key:
        for reference in found:
            if reference.key == key:
                return reference
    if not found:
        return None
    return found[min(max(fallback, 0), len(found) - 1)]


def grouped(found: list[Reference]) -> dict[str, list[Reference]]:
    """The references by the heading they sit under in the picker."""
    out: dict[str, list[Reference]] = {}
    for reference in found:
        out.setdefault(reference.group, []).append(reference)
    return out
