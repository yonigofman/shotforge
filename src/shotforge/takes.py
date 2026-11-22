"""Takes: every generation is kept, approval copies it to the fixed name.

Nothing produced is ever deleted. A take gets the next number, and approving
one copies it to the name the later stages refer to (``first.png``,
``shot.mp4``) — so two frames can never overwrite each other.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from shotforge.paths import APPROVED_FRAME, APPROVED_VIDEO, ShotPaths


class TakeKind(StrEnum):
    frame = "frame"
    video = "video"

    @property
    def folder_name(self) -> str:
        return "frames" if self is TakeKind.frame else "video"

    @property
    def stem(self) -> str:
        return "first" if self is TakeKind.frame else "v"

    @property
    def approved_name(self) -> str:
        return APPROVED_FRAME if self is TakeKind.frame else APPROVED_VIDEO

    @property
    def extensions(self) -> tuple[str, ...]:
        if self is TakeKind.frame:
            return (".png", ".jpg", ".jpeg", ".webp")
        return (".mp4", ".mov", ".webm", ".m4v")

    @property
    def default_extension(self) -> str:
        return ".png" if self is TakeKind.frame else ".mp4"


@dataclass(frozen=True)
class Take:
    kind: TakeKind
    number: int
    path: Path

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def modified(self) -> datetime:
        return datetime.fromtimestamp(self.path.stat().st_mtime, tz=UTC)

    def qa_report(self, shot: ShotPaths) -> Path:
        return shot.qa / f"{self.kind.value}_v{self.number}.json"


def _pattern(kind: TakeKind) -> re.Pattern[str]:
    if kind is TakeKind.frame:
        return re.compile(r"^first_v(\d+)$")
    return re.compile(r"^v(\d+)$")


def folder(shot: ShotPaths, kind: TakeKind) -> Path:
    return shot.frames if kind is TakeKind.frame else shot.video


def list_takes(shot: ShotPaths, kind: TakeKind) -> list[Take]:
    """Every take of this kind, oldest first. The approved copy is not a take."""
    target = folder(shot, kind)
    if not target.is_dir():
        return []
    pattern = _pattern(kind)
    takes: list[Take] = []
    for path in sorted(target.iterdir()):
        if path.suffix.lower() not in kind.extensions:
            continue
        match = pattern.match(path.stem)
        if match:
            takes.append(Take(kind=kind, number=int(match.group(1)), path=path))
    return sorted(takes, key=lambda t: t.number)


def next_number(shot: ShotPaths, kind: TakeKind) -> int:
    takes = list_takes(shot, kind)
    return (takes[-1].number + 1) if takes else 1


def take_path(shot: ShotPaths, kind: TakeKind, number: int, extension: str) -> Path:
    extension = extension if extension.startswith(".") else f".{extension}"
    stem = f"first_v{number}" if kind is TakeKind.frame else f"v{number}"
    return folder(shot, kind) / f"{stem}{extension.lower()}"


def add_take(shot: ShotPaths, kind: TakeKind, source: Path, *, move: bool = False) -> Take:
    """File an incoming download as the next numbered take."""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    extension = source.suffix.lower()
    if extension not in kind.extensions:
        raise ValueError(
            f"{source.name} is not a {kind.value} file "
            f"(expected one of {', '.join(kind.extensions)})"
        )
    target_dir = folder(shot, kind)
    target_dir.mkdir(parents=True, exist_ok=True)
    number = next_number(shot, kind)
    target = take_path(shot, kind, number, extension)
    if move:
        shutil.move(str(source), target)
    else:
        shutil.copy2(source, target)
    return Take(kind=kind, number=number, path=target)


def get_take(shot: ShotPaths, kind: TakeKind, number: int) -> Take:
    for take in list_takes(shot, kind):
        if take.number == number:
            return take
    raise FileNotFoundError(f"no {kind.value} take v{number} in {folder(shot, kind)}")


def approved_path(shot: ShotPaths, kind: TakeKind) -> Path:
    return folder(shot, kind) / kind.approved_name


def approve_take(shot: ShotPaths, kind: TakeKind, number: int) -> Path:
    """Copy a take to the fixed name. The take itself stays where it is."""
    take = get_take(shot, kind, number)
    target = approved_path(shot, kind)
    if take.path.suffix.lower() != target.suffix.lower():
        # Keep the fixed name exact: later stages attach `first.png` by name.
        raise ValueError(
            f"take {take.name} is {take.path.suffix} but the approved name is "
            f"{target.name} — convert it first"
        )
    shutil.copy2(take.path, target)
    return target


def approved_take_number(shot: ShotPaths, kind: TakeKind) -> int | None:
    """Which take the approved copy came from, matched by content size and mtime."""
    target = approved_path(shot, kind)
    if not target.is_file():
        return None
    stat = target.stat()
    for take in reversed(list_takes(shot, kind)):
        take_stat = take.path.stat()
        if take_stat.st_size == stat.st_size and abs(take_stat.st_mtime - stat.st_mtime) < 2:
            return take.number
    return None
