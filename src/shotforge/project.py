"""A project on disk: the folder tree, and typed access to every file in it.

The folder is the source of truth. If the cockpit is closed, the project is
still readable and complete.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from shotforge.models import (
    Character,
    Location,
    ProjectConfig,
    Prop,
    Scene,
    Shot,
    Style,
)
from shotforge.paths import ProjectPaths
from shotforge.yamlio import ProjectFileError, dump, load

KINDS = ("character", "location", "prop")
_MODELS: dict[str, type] = {"character": Character, "location": Location, "prop": Prop}

LFS_PATTERNS = ("*.png", "*.jpg", "*.jpeg", "*.mp4", "*.mov", "*.wav", "*.blend")

_GITIGNORE = """\
# Downloads land here and are sorted into the tree; nothing to keep in Git.
_inbox/

# Runtime cache (state and the approval log itself are worth keeping).
.shotforge/cache/
"""

_SOURCE_PLACEHOLDER = """\
# Chapter 1

Paste the chapter here, as it is written in the book. One file per chapter
(ch01.md, ch02.md …). Nothing in this folder is ever rewritten by Shotforge.
"""


class ProjectError(Exception):
    pass


@dataclass
class Project:
    """Loaded project: its config plus the paths of everything else."""

    paths: ProjectPaths
    config: ProjectConfig

    # -- opening and creating ---------------------------------------------
    @classmethod
    def open(cls, root: Path) -> Project:
        paths = ProjectPaths(Path(root).resolve())
        if not paths.config.is_file():
            raise ProjectError(f"{root} holds no {paths.config.name}")
        return cls(paths=paths, config=load(paths.config, ProjectConfig))

    @classmethod
    def discover(cls, start: Path | None = None) -> Project:
        paths = ProjectPaths.find(start)
        if paths is None:
            raise ProjectError(
                "no project here or in any folder above it — run `shotforge new <name>` first"
            )
        return cls.open(paths.root)

    @classmethod
    def create(
        cls,
        root: Path,
        name: str | None = None,
        *,
        language: str = "ru",
        aspect_ratio: str = "16:9",
        fps: int = 24,
        media: str = "lfs",
        exist_ok: bool = False,
    ) -> Project:
        """Build the fixed folder tree, so every file has one place and one name."""
        root = Path(root).resolve()
        if (root / "project.yaml").exists() and not exist_ok:
            raise ProjectError(f"{root} is already a Shotforge project")
        name = name or root.name
        config = ProjectConfig(
            name=name, language=language, aspect_ratio=aspect_ratio, fps=fps, media=media
        )
        paths = ProjectPaths(root)

        folders = [
            paths.source,
            paths.bible,
            *[paths.bible_kind(k) for k in KINDS],
            paths.design,
            *[paths.design / f"{k}s" for k in KINDS],
            paths.blender,
            paths.textures,
            paths.blender / "renders",
            paths.scenes,
            paths.edit,
            paths.inbox,
            paths.state_dir,
            paths.skills,
        ]
        for folder in folders:
            folder.mkdir(parents=True, exist_ok=True)

        dump(paths.config, config)
        if not paths.style.exists():
            dump(
                paths.style,
                Style(
                    art_style="(fill in: art style of the series)",
                    film_stock=None,
                    palette=[],
                    world_rules=[],
                    negative=["no subtitles", "no text", "no watermark"],
                ),
            )
        source_file = paths.source / "ch01.md"
        if not source_file.exists():
            source_file.write_text(_SOURCE_PLACEHOLDER, encoding="utf-8")
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(_GITIGNORE, encoding="utf-8")
        if media == "lfs":
            attributes = root / ".gitattributes"
            if not attributes.exists():
                attributes.write_text(
                    "".join(f"{pat} filter=lfs diff=lfs merge=lfs -text\n" for pat in LFS_PATTERNS),
                    encoding="utf-8",
                )
        keep = paths.inbox / ".gitkeep"
        keep.touch(exist_ok=True)
        return cls(paths=paths, config=config)

    # -- source and bible --------------------------------------------------
    def chapter_text(self, filename: str) -> str:
        if not filename or Path(filename).name != filename:
            raise ProjectError("Choose a chapter filename in 00_source.")
        text = (self.paths.source / filename).read_text(encoding="utf-8-sig")
        if not text.strip() or text.strip() == _SOURCE_PLACEHOLDER.strip():
            raise ProjectError(f"Add the book text to 00_source/{filename} before generating.")
        return text

    @property
    def name(self) -> str:
        return self.config.name

    def style(self) -> Style:
        return load(self.paths.style, Style)

    def entity(self, kind: str, entity_id: str):
        if kind not in KINDS:
            raise ProjectError(f"unknown bible kind {kind!r}")
        path = self.paths.entity_file(kind, entity_id)
        if not path.is_file():
            raise ProjectError(f"no {kind} {entity_id!r} in the bible ({path})")
        return load(path, _MODELS[kind])

    def entities(self, kind: str) -> dict[str, Character | Location | Prop]:
        return {eid: self.entity(kind, eid) for eid in self.paths.entity_ids(kind)}

    def character(self, character_id: str) -> Character:
        return self.entity("character", character_id)  # type: ignore[return-value]

    def characters(self) -> dict[str, Character]:
        return self.entities("character")  # type: ignore[return-value]

    def location(self, location_id: str) -> Location:
        return self.entity("location", location_id)  # type: ignore[return-value]

    def props(self) -> dict[str, Prop]:
        return self.entities("prop")  # type: ignore[return-value]

    def save_entity(self, kind: str, entity, *, create_only: bool = False) -> Path:
        path = self.paths.entity_file(kind, entity.id)
        dump(path, entity, create_only=create_only)
        return path

    # -- scenes and shots --------------------------------------------------
    def scene_ids(self) -> list[str]:
        return self.paths.scene_ids()

    def scene(self, scene_id: str) -> Scene:
        path = self.paths.scene(scene_id).spec
        if not path.is_file():
            raise ProjectError(f"no scene {scene_id!r} ({path})")
        return load(path, Scene)

    def scenes(self) -> dict[str, Scene]:
        return {sid: self.scene(sid) for sid in self.scene_ids()}

    def shot_ids(self, scene_id: str) -> list[str]:
        return self.paths.scene(scene_id).shot_ids()

    def shot(self, scene_id: str, shot_id: str) -> Shot:
        path = self.paths.shot(scene_id, shot_id).spec
        if not path.is_file():
            raise ProjectError(f"no shot {shot_id!r} in scene {scene_id!r} ({path})")
        return load(path, Shot)

    def shots(self, scene_id: str) -> list[Shot]:
        return [self.shot(scene_id, sid) for sid in self.shot_ids(scene_id)]

    def previous_shot(self, scene_id: str, shot_id: str) -> Shot | None:
        ids = self.shot_ids(scene_id)
        if shot_id not in ids:
            return None
        index = ids.index(shot_id)
        return self.shot(scene_id, ids[index - 1]) if index else None

    def next_shot(self, scene_id: str, shot_id: str) -> Shot | None:
        ids = self.shot_ids(scene_id)
        if shot_id not in ids:
            return None
        index = ids.index(shot_id)
        return self.shot(scene_id, ids[index + 1]) if index + 1 < len(ids) else None

    def save_scene(self, scene: Scene, *, create_only: bool = False) -> Path:
        paths = self.paths.scene(scene.id)
        paths.shots_dir.mkdir(parents=True, exist_ok=True)
        dump(paths.spec, scene, create_only=create_only)
        return paths.spec

    def save_shot(self, scene_id: str, shot: Shot, *, create_only: bool = False) -> Path:
        shot_paths = self.paths.shot(scene_id, shot.id)
        for folder in shot_paths.dirs():
            folder.mkdir(parents=True, exist_ok=True)
        dump(shot_paths.spec, shot, create_only=create_only)
        return shot_paths.spec

    def shot_refs(self, scene_id: str, shot_id: str) -> dict[str, Path]:
        """The files a prompt for this shot attaches, by role."""
        shot_paths = self.paths.shot(scene_id, shot_id)
        refs: dict[str, Path] = {}
        blockout = self.paths.blockout(scene_id, shot_id)
        if blockout.is_file():
            refs["blockout"] = blockout
        for character_id in self.shot(scene_id, shot_id).characters:
            sheet = self.paths.sheet("character", character_id)
            if sheet.is_file():
                refs[f"sheet:{character_id}"] = sheet
        previous = self.previous_shot(scene_id, shot_id)
        if previous is not None:
            last_frame = self.paths.shot(scene_id, previous.id).last_frame
            if last_frame.is_file():
                refs["last_frame"] = last_frame
        if shot_paths.approved_frame.is_file():
            refs["approved_frame"] = shot_paths.approved_frame
        return refs

    def validate(self) -> list[str]:
        """Load every file in the project and collect the schema errors."""
        problems: list[str] = []
        for loader in (self.style,):
            try:
                loader()
            except ProjectFileError as exc:
                problems.append(str(exc))
        for kind in KINDS:
            for entity_id in self.paths.entity_ids(kind):
                try:
                    self.entity(kind, entity_id)
                except ProjectFileError as exc:
                    problems.append(str(exc))
        for scene_id in self.scene_ids():
            try:
                self.scene(scene_id)
            except ProjectFileError as exc:
                problems.append(str(exc))
                continue
            for shot_id in self.shot_ids(scene_id):
                try:
                    self.shot(scene_id, shot_id)
                except ProjectFileError as exc:
                    problems.append(str(exc))
        return problems


def slugify(name: str) -> str:
    """A folder name from a project title: lowercase, underscores, no surprises."""
    slug = re.sub(r"[^\w\-]+", "_", name.strip(), flags=re.UNICODE).strip("_").lower()
    return slug or "project"
