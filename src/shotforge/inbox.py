"""``_inbox/``: everything you download lands here and gets filed.

The cockpit asks one question — which shot is this? — defaulting to the active
shot, and then the file becomes the next numbered take in the right folder.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import BinaryIO

from shotforge import actions
from shotforge.media import write_design_png
from shotforge.paths import ProjectPaths
from shotforge.project import Project
from shotforge.takes import Take, TakeKind, add_take

IGNORED_NAMES = {".gitkeep", ".DS_Store", "Thumbs.db"}
PARTIAL_SUFFIXES = (".part", ".crdownload", ".download", ".tmp")


class DesignAsset(StrEnum):
    sheet = "sheet"
    ref = "ref"

    def path(self, paths: ProjectPaths, kind: str, entity_id: str) -> Path:
        if self is DesignAsset.sheet:
            return paths.sheet(kind, entity_id)
        return paths.ref(kind, entity_id)


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


@contextmanager
def stage_upload(project: Project, name: str, stream: BinaryIO) -> Iterator[Path]:
    """Stage a browser upload in the inbox without replacing an existing download."""
    staging = project.paths.inbox
    staging.mkdir(parents=True, exist_ok=True)
    basename = Path(name.replace("\\", "/")).name
    if basename in {"", ".", ".."}:
        raise ValueError("upload needs a file name")
    with TemporaryDirectory(prefix=".upload-", dir=staging) as directory:
        source = Path(directory) / basename
        with source.open("wb") as file:
            shutil.copyfileobj(stream, file)
        yield source


def file_to_design(
    project: Project, inbox_file: Path, kind: str, entity_id: str, asset: DesignAsset
) -> Path:
    """File one inbox image under its fixed design filename."""
    project.entity(kind, entity_id)
    inbox_file = Path(inbox_file)
    if (
        not inbox_file.is_file()
        or inbox_file.is_symlink()
        or not inbox_file.resolve().is_relative_to(project.paths.inbox.resolve())
    ):
        raise FileNotFoundError(f"{inbox_file.name} is not a file in the inbox")
    if classify(inbox_file) is not TakeKind.frame:
        raise ValueError("choose a PNG, JPEG or WebP image")

    target = asset.path(project.paths, kind, entity_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".filing-", dir=target.parent) as directory:
        staged = Path(directory) / target.name
        write_design_png(inbox_file, staged)
        staged.replace(target)
    inbox_file.unlink()
    return target


def file_to_shot(
    project: Project,
    inbox_file: Path,
    scene_id: str,
    shot_id: str,
    kind: TakeKind | None = None,
    *,
    vision: bool | None = None,
) -> Take:
    """Move one inbox file in as the next take and run QA on new frames."""
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
    take = add_take(shot_paths, kind, inbox_file, move=True)
    if kind is TakeKind.frame:
        actions.run_qa(project, scene_id, shot_id, kind, take.number, vision=vision)
    return take


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
