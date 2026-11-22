"""Prepare an image-generator prompt and its Blender surface assignment."""

from pathlib import Path

from shotforge import design
from shotforge.models.common import as_plain
from shotforge.models.materials import Materials
from shotforge.project import Project
from shotforge.skills import find_skill, render_prompt
from shotforge.state import ProjectState
from shotforge.yamlio import render_yaml


def build_prompt(
    project: Project, surface: str, name: str, uv_scale: float, source: str | None = None,
) -> tuple[Path, str, str]:
    entry = {"texture": f"{name}.png", "uv_scale": uv_scale}
    if source is not None:
        kind, _, entity_id = source.partition(".")
        entry["source"] = {"kind": kind, "id": entity_id}
    assignments = Materials.model_validate({surface: entry})
    origin = assignments.source_for(surface)
    entity = project.entity(origin.kind, origin.id)
    sheet = project.paths.sheet(origin.kind, origin.id)
    if not sheet.is_file() or not sheet.resolve().is_relative_to(project.paths.root):
        raise ValueError(f"place the approved sheet at {project.paths.rel(sheet)} first")
    approved = ProjectState(project.paths).approved_design_revision(origin.kind, origin.id)
    if approved is None or approved != design.revision(project, origin.kind, origin.id, entity):
        raise ValueError(f"approve the current design for {origin.kind} {origin.id!r} in the Bible")
    skill = find_skill("texture-from-sheet", project.paths.skills)
    prompt = render_prompt(skill, {
        "sheet": project.paths.rel(sheet),
        "entity": as_plain(entity),
        "surface": surface.rsplit(".", 1)[1],
        "filename": assignments.root[surface].texture,
    })
    return sheet, prompt, render_yaml(assignments)
