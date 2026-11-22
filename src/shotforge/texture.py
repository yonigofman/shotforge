"""Prepare an image-generator prompt and its Blender surface assignment."""

from pathlib import Path

import yaml

from shotforge.models.common import as_plain
from shotforge.models.materials import Materials
from shotforge.project import Project
from shotforge.skills import find_skill, render_prompt


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
    skill = find_skill("texture-from-sheet", project.paths.skills)
    prompt = render_prompt(skill, {
        "sheet": project.paths.rel(sheet),
        "entity": as_plain(entity),
        "surface": surface.rsplit(".", 1)[1],
        "filename": assignments.root[surface].texture,
    })
    return sheet, prompt, yaml.safe_dump(as_plain(assignments), sort_keys=False)
