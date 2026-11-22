"""Read-only craft proposals and the create-only writes that a user approves."""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path
from typing import TypeVar

from pydantic import Field, ValidationError

from shotforge.engine import Engine, EngineError, get_engine
from shotforge.models import Character, Location, Prop, Scene, Shot, ShotStatus
from shotforge.models.common import ID_RE, Entity, SFModel, as_plain
from shotforge.project import KINDS, Project, ProjectError
from shotforge.skills import find_skill
from shotforge.skills.registry import SkillError
from shotforge.tools import ToolError
from shotforge.yamlio import ProjectFileError, render_yaml


class BreakdownError(Exception):
    """The source or proposal needs attention before it can be saved."""


@dataclass(frozen=True)
class Chapter:
    name: str
    text: str


class BookBreakdown(SFModel):
    scenes: list[Scene] = Field(min_length=1)
    characters: list[Character]
    props: list[Prop]
    locations: list[Location]

    def entities(self, kind: str) -> list[Character] | list[Prop] | list[Location]:
        return getattr(self, f"{kind}s")


class ShotList(SFModel):
    shots: list[Shot] = Field(min_length=1)


def chapter_names(project: Project) -> list[str]:
    source = project.paths.source
    if not source.is_dir():
        return []
    extensions = {".md", ".markdown", ".txt"}
    return sorted(
        p.name for p in source.iterdir() if p.suffix.lower() in extensions and p.is_file()
    )


def read_chapter(project: Project, chapter: str | None) -> Chapter:
    names = chapter_names(project)
    if chapter is None:
        if len(names) != 1:
            raise BreakdownError("Use --chapter to choose a chapter file in 00_source.")
        chapter = names[0]
    if chapter not in names:
        matches = [name for name in names if Path(name).stem == chapter]
        if len(matches) != 1:
            choices = ", ".join(names) or "no chapter files"
            raise BreakdownError(f"Choose a chapter filename in 00_source. Found: {choices}")
        chapter = matches[0]
    try:
        return Chapter(chapter, project.chapter_text(chapter))
    except (ProjectError, OSError, UnicodeError) as exc:
        raise BreakdownError(str(exc)) from exc


def propose_book(
    project: Project, chapter: Chapter, *, engine: Engine | None = None
) -> BookBreakdown:
    bible = {kind: as_plain(project.entities(kind)) for kind in KINDS}
    book = _run(
        project,
        engine,
        "book-breakdown",
        {
            "chapter": chapter.name,
            "chapter_text": chapter.text,
            "language": project.config.language,
            "existing_bible": bible,
            "existing_scene_ids": project.scene_ids(),
        },
        BookBreakdown,
    )
    known = {}
    for kind in KINDS:
        entities = book.entities(kind)
        _check_catalog(entities, kind)
        known[kind] = set(bible[kind]) | {entity.id for entity in entities}
    _check_catalog(book.scenes, "scene")
    for scene in book.scenes:
        scene.chapter = chapter.name
        _check_refs(
            scene.id,
            "character",
            [*scene.characters, *(s.char for s in scene.seating)],
            known["character"],
        )
        _check_refs(scene.id, "location", [scene.location], known["location"])
        _check_refs(scene.id, "prop", scene.props, known["prop"])
    return book


def propose_shots(
    project: Project,
    scene: Scene,
    chapter: Chapter,
    *,
    book: BookBreakdown | None = None,
    engine: Engine | None = None,
) -> list[Shot]:
    characters = project.entities("character")
    if book is not None:
        characters = {**{c.id: c for c in book.characters}, **characters}
    shot_list = _run(
        project,
        engine,
        "scene-to-shots",
        {
            "scene": as_plain(scene),
            "chapter": chapter.name,
            "chapter_text": chapter.text,
            "language": project.config.language,
            "characters": as_plain(characters),
            "existing_shot_ids": project.shot_ids(scene.id),
        },
        ShotList,
    )
    _check_ids(shot_list.shots, f"shot in {scene.id}")
    shots = []
    for shot in shot_list.shots:
        # Status belongs to the pipeline. A skill can only propose a new breakdown.
        shot.status = ShotStatus.breakdown
        shot = validate_shot(shot, known_characters=set(characters))
        shots.append(shot)
    return shots


def validate_shot(
    shot: Shot, *, known_characters: set[str], beats: list[dict] | None = None
) -> Shot:
    """Validate an entire shot, including any edits made during review."""
    payload = as_plain(shot)
    if beats is not None:
        payload["beats"] = beats
    try:
        checked = Shot.model_validate(payload)
    except ValidationError as exc:
        raise BreakdownError(f"{shot.id}: {_validation_detail(exc)}") from exc
    if not checked.beats:
        raise BreakdownError(f"{shot.id} needs at least one beat.")
    for beat in checked.beats:
        if beat.line_overruns():
            raise BreakdownError(f"{shot.id}: shorten the line or give beat {beat.t} more time.")
    _check_refs(
        checked.id, "character", [*checked.characters, *checked.speakers()], known_characters
    )
    return checked


def load_scene(project: Project, scene_id: str) -> Scene:
    if not ID_RE.fullmatch(scene_id):
        raise BreakdownError(f"Invalid scene id {scene_id!r}.")
    try:
        scene = project.scene(scene_id)
    except (ProjectError, ProjectFileError, OSError) as exc:
        raise BreakdownError(str(exc)) from exc
    if scene.id != scene_id:
        raise BreakdownError(f"{scene_id}/scene.yaml declares a different id ({scene.id}).")
    return scene


def _check_refs(owner: str, kind: str, ids: list[str], known: set[str]) -> None:
    if unknown := set(ids) - known:
        raise BreakdownError(f"{owner} references unknown {kind} ids: {', '.join(sorted(unknown))}")


def _check_ids(records: list, label: str) -> None:
    seen = set()
    for record in records:
        if record.id in seen:
            raise BreakdownError(f"Duplicate {label} id {record.id!r} in the proposal.")
        seen.add(record.id)


def _check_catalog(
    records: list[Scene] | list[Character] | list[Prop] | list[Location], label: str
) -> None:
    _check_ids(records, label)
    for record in records:
        if not any(record.from_book) and not any(record.design_choices):
            raise BreakdownError(f"{label} {record.id} needs from_book or design_choices notes.")


T = TypeVar("T", bound=SFModel)


def _run(project: Project, engine: Engine | None, name: str, inputs: dict, schema: type[T]) -> T:
    try:
        skill = find_skill(name, project.paths.skills)
        engine = engine or get_engine(project.config.engine)
        result = engine.run_skill(skill, inputs, cwd=project.paths.root)
        if result.data.get("is_error"):
            raise EngineError(result.text[:500])
        payload = result.data.get("parsed")
        if payload is None:
            payload = json.loads(result.text)
        if isinstance(payload, dict) and isinstance(error := payload.get("error"), str):
            raise EngineError(error[:500])
        return schema.model_validate(payload)
    except ValidationError as exc:
        detail = _validation_detail(exc)
        raise BreakdownError(f"{name} returned an invalid proposal. {detail}") from exc
    except json.JSONDecodeError as exc:
        raise BreakdownError(f"{name} did not return a JSON proposal.") from exc
    except (EngineError, SkillError, ToolError, OSError) as exc:
        raise BreakdownError(f"{name}: {exc}") from exc


def _validation_detail(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(map(str, err['loc'])) or '(root)'}: {err['msg']}"
        for err in exc.errors(include_input=False, include_url=False)
    )


@dataclass(frozen=True)
class ProposedFile:
    path: Path
    spec: Entity | Scene | Shot
    kind: str = ""
    scene_id: str = ""

    def diff(self, project: Project) -> str:
        return "".join(
            unified_diff(
                [],
                render_yaml(self.spec).splitlines(keepends=True),
                fromfile="/dev/null",
                tofile=project.paths.rel(self.path),
            )
        )

    def save(self, project: Project) -> None:
        if isinstance(self.spec, Scene):
            project.save_scene(self.spec, create_only=True)
        elif isinstance(self.spec, Shot):
            project.save_shot(self.scene_id, self.spec, create_only=True)
        else:
            project.save_entity(self.kind, self.spec, create_only=True)


@dataclass
class WritePlan:
    files: list[ProposedFile]
    collisions: list[Path]

    @classmethod
    def for_book(cls, project: Project, book: BookBreakdown) -> WritePlan:
        entities = [
            ProposedFile(project.paths.entity_file(kind, entity.id), entity, kind=kind)
            for kind in KINDS
            for entity in book.entities(kind)
        ]
        scenes = [ProposedFile(project.paths.scene(scene.id).spec, scene) for scene in book.scenes]
        return cls._preview([*entities, *scenes])

    @classmethod
    def for_shots(cls, project: Project, scene_id: str, shots: list[Shot]) -> WritePlan:
        return cls._preview(
            [
                ProposedFile(project.paths.shot(scene_id, shot.id).spec, shot, scene_id=scene_id)
                for shot in shots
            ]
        )

    @classmethod
    def _preview(cls, files: list[ProposedFile]) -> WritePlan:
        plan = cls([], [])
        for entry in files:
            if entry.path.exists() or entry.path.is_symlink():
                plan.collisions.append(entry.path)
            else:
                plan.files.append(entry)
        return plan

    def add(self, plan: WritePlan) -> None:
        self.files.extend(plan.files)
        self.collisions.extend(plan.collisions)

    def save(self, project: Project) -> tuple[list[Path], list[Path]]:
        """Apply the reviewed files, checking again for collisions at the write."""
        written, collisions, blocked_scenes = [], [], set()
        for entry in self.files:
            if entry.scene_id in blocked_scenes:
                collisions.append(entry.path)
                continue
            try:
                entry.save(project)
            except FileExistsError:
                collisions.append(entry.path)
                if isinstance(entry.spec, Scene):
                    # An identical scene from a partial save can safely receive its shots.
                    try:
                        existing = project.scene(entry.spec.id)
                    except (ProjectError, ProjectFileError):
                        existing = None
                    if existing != entry.spec:
                        blocked_scenes.add(entry.spec.id)
            else:
                written.append(entry.path)
        return written, collisions
