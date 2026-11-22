"""Recently opened project folders. Project data stays in each folder."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from filelock import FileLock

from shotforge.project import Project, ProjectError
from shotforge.yamlio import write_text


@dataclass(frozen=True)
class ProjectEntry:
    path: Path
    name: str

    @property
    def key(self) -> str:
        return sha256(os.fsencode(os.path.normcase(self.path))).hexdigest()[:32]


class ProjectRegistry:
    def __init__(self, path: Path | None = None) -> None:
        override = os.environ.get("SHOTFORGE_REGISTRY")
        if path is None:
            path = Path(override) if override else Path.home() / ".shotforge" / "projects.json"
        self.path = path.expanduser().resolve()

    def entries(self) -> list[ProjectEntry]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        try:
            if not isinstance(data, list):
                raise TypeError
            entries = [ProjectEntry(Path(item["path"]), item["name"]) for item in data]
            if any(not entry.path.is_absolute() or not isinstance(entry.name, str)
                   for entry in entries):
                raise ValueError
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectError(f"{self.path} must list project names and absolute paths.") from exc
        return entries

    def find(self, key: str) -> ProjectEntry | None:
        return next((entry for entry in self.entries() if entry.key == key), None)

    def remember(self, project: Project) -> ProjectEntry:
        entry = ProjectEntry(project.paths.root, project.name)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.path) + ".lock", timeout=5):
            entries = [entry, *(item for item in self.entries() if item.key != entry.key)]
            data = [{"path": str(item.path), "name": item.name} for item in entries]
            write_text(self.path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        return entry
