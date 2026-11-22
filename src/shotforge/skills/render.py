"""Rendering a prompt from a skill template — no model involved.

Most prompts are templates filled from the project's YAML, which is why a
scene does not stop when the Claude Code limit is reached. The judgment
skills (breakdown, cinematography, QA) go through the engine instead.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from shotforge.models.common import as_plain
from shotforge.pipeline import Attachment
from shotforge.project import Project
from shotforge.skills.registry import Skill, SkillError, bundled_root


def _environment(skill: Skill) -> Environment:
    env = Environment(
        loader=FileSystemLoader([skill.root, bundled_root()]),
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        undefined=StrictUndefined,
        autoescape=False,
    )
    env.filters["timecode"] = _timecode
    env.filters["n"] = _number
    return env


def _number(value: float | int) -> str:
    """Write 15 rather than 15.0 — these are read by a person, not parsed."""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _timecode(seconds: float) -> str:
    seconds = float(seconds)
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes):02d}:{rest:04.1f}" if minutes else f"{rest:.1f}s"


def shot_context(
    project: Project,
    scene_id: str,
    shot_id: str,
    *,
    attachments: list[Attachment] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything a prompt template may read, as plain data."""
    scene = project.scene(scene_id)
    shot = project.shot(scene_id, shot_id)
    previous = project.previous_shot(scene_id, shot_id)
    characters = {cid: project.character(cid) for cid in shot.characters}
    try:
        location = project.location(scene.location)
    except Exception:  # a location file is optional while a scene is young
        location = None
    context: dict[str, Any] = {
        "project": as_plain(project.config),
        "style": as_plain(project.style()),
        "scene": as_plain(scene),
        "shot": as_plain(shot),
        "previous_shot": as_plain(previous) if previous else None,
        "characters": {cid: as_plain(c) for cid, c in characters.items()},
        "tags": {cid: (c.tag or f"@{cid.title()}") for cid, c in characters.items()},
        "location": as_plain(location) if location else None,
        "attachments": [
            {
                "order": a.order,
                "label": a.label,
                "name": a.name,
                "path": project.paths.rel(a.path),
                "exists": a.exists,
            }
            for a in (attachments or [])
        ],
    }
    context.update(extra or {})
    return context


def render_prompt(skill: Skill, context: dict[str, Any]) -> str:
    """Fill the skill's template; the skill's rules are always in scope."""
    if not skill.template_path.is_file():
        raise SkillError(f"skill {skill.name} has no template.j2 — it is not a prompt skill")
    template = _environment(skill).get_template(skill.template_path.name)
    payload = dict(context)
    payload.setdefault("rules", skill.constraints())
    payload.setdefault("rule_notes", skill.rules())
    text = template.render(**payload)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def next_prompt_version(folder: Path, skill_name: str) -> int:
    if not folder.is_dir():
        return 1
    pattern = re.compile(rf"^{re.escape(skill_name)}_v(\d+)$")
    versions = [int(m.group(1)) for p in folder.glob("*.txt") if (m := pattern.match(p.stem))]
    return max(versions) + 1 if versions else 1


def save_prompt(project: Project, scene_id: str, shot_id: str, skill: Skill, text: str) -> Path:
    """Keep every prompt that was generated, numbered, next to the shot."""
    folder = project.paths.shot(scene_id, shot_id).prompts
    folder.mkdir(parents=True, exist_ok=True)
    version = next_prompt_version(folder, skill.name)
    while True:
        path = folder / f"{skill.name}_v{version}.txt"
        try:
            with path.open("x", encoding="utf-8") as prompt:
                prompt.write(text)
        except FileExistsError:
            version += 1
        else:
            return path


def latest_prompt(project: Project, scene_id: str, shot_id: str, skill_name: str) -> Path | None:
    folder = project.paths.shot(scene_id, shot_id).prompts
    if not folder.is_dir():
        return None
    pattern = re.compile(rf"^{re.escape(skill_name)}_v(\d+)$")
    matches = [(int(m.group(1)), p) for p in folder.glob("*.txt") if (m := pattern.match(p.stem))]
    return max(matches)[1] if matches else None
