"""File downloads from ``_inbox/`` as shot or texture takes."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from shotforge.models.materials import texture_filename
from shotforge.project import Project
from shotforge.takes import Take, TakeKind, add_take

IGNORED_NAMES = {".gitkeep", ".DS_Store", "Thumbs.db"}
PARTIAL_SUFFIXES = (".part", ".crdownload", ".download", ".tmp")


@dataclass
class InboxFile:
    path: Path
    kind: TakeKind | None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def size_kb(self) -> int:
        return max(1, round(self.path.stat().st_size / 1024))

    @property
    def modified(self) -> datetime:
        return datetime.fromtimestamp(self.path.stat().st_mtime, tz=UTC)

    @property
    def filed_as(self) -> str:
        return self.kind.value if self.kind else "unknown"


def classify(path: Path) -> TakeKind | None:
    suffix = path.suffix.lower()
    if suffix in TakeKind.frame.extensions:
        return TakeKind.frame
    if suffix in TakeKind.video.extensions:
        return TakeKind.video
    return None


def scan(project: Project) -> list[InboxFile]:
    """What is waiting in the inbox, newest first; partial downloads skipped."""
    inbox = project.paths.inbox
    if not inbox.is_dir():
        return []
    found: list[InboxFile] = []
    for path in inbox.iterdir():
        if not path.is_file() or path.name in IGNORED_NAMES or path.name.startswith("."):
            continue
        if path.suffix.lower() in PARTIAL_SUFFIXES:
            continue
        found.append(InboxFile(path=path, kind=classify(path)))
    return sorted(found, key=lambda f: f.path.stat().st_mtime, reverse=True)


def file_to_shot(
    project: Project,
    inbox_file: Path,
    scene_id: str,
    shot_id: str,
    kind: TakeKind | None = None,
) -> Take:
    """Move one inbox file in as the next take of a shot."""
    inbox_file = Path(inbox_file)
    if not inbox_file.is_file():
        raise FileNotFoundError(inbox_file)
    kind = kind or classify(inbox_file)
    if kind is None:
        raise ValueError(
            f"{inbox_file.name}: not an image or a video Shotforge files "
            f"({', '.join(TakeKind.frame.extensions + TakeKind.video.extensions)})"
        )
    shot_paths = project.paths.shot(scene_id, shot_id)
    if not shot_paths.spec.is_file():
        raise FileNotFoundError(f"no shot {shot_id} in scene {scene_id}")
    return add_take(shot_paths, kind, inbox_file, move=True)


def file_to_texture(project: Project, inbox_file: Path, name: str) -> Path:
    """Keep a numbered PNG take and publish it to the material's fixed path."""
    filename = texture_filename(f"{name}.png")
    inbox = project.paths.inbox.resolve()
    if inbox_file.parent.resolve() != inbox or inbox_file.is_symlink():
        raise ValueError("select a file directly in _inbox")
    if inbox_file.suffix.lower() != ".png":
        raise ValueError("texture takes must be PNG files")

    textures = project.paths.textures
    takes = textures / "takes" / name
    takes.mkdir(parents=True, exist_ok=True)
    target = textures / filename
    with TemporaryDirectory(prefix=".texture-", dir=textures) as scratch:
        staged = Path(scratch) / "active.png"
        shutil.copyfile(inbox_file, staged)
        with staged.open("rb") as image:
            if image.read(8) != b"\x89PNG\r\n\x1a\n":
                raise ValueError(f"{inbox_file.name} is not a PNG file")
        archive = Path(scratch) / "archive.png"
        shutil.copyfile(staged, archive)
        versions = (
            int(path.stem[1:]) for path in takes.glob("v*.png") if path.stem[1:].isdecimal()
        )
        version = max(versions, default=0) + 1
        while True:
            take = takes / f"v{version}.png"
            try:
                os.link(archive, take)
                break
            except FileExistsError:
                version += 1
        try:
            staged.replace(target)
        except OSError:
            take.unlink(missing_ok=True)
            raise
    inbox_file.unlink(missing_ok=True)
    return target


def drain(
    project: Project, scene_id: str, shot_id: str, kind: TakeKind | None = None
) -> list[Take]:
    """File everything in the inbox to one shot (the active one, usually)."""
    filed: list[Take] = []
    for item in scan(project):
        if kind is not None and item.kind is not kind:
            continue
        if item.kind is None:
            continue
        filed.append(file_to_shot(project, item.path, scene_id, shot_id, item.kind))
    return filed
