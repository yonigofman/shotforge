"""Skills: one per action in the pipeline, in the Agent Skills format.

A skill is a folder with ``SKILL.md`` (when to use it, inputs, outputs), an
optional ``rules.md`` (the lessons that accumulated), ``template.j2`` (the
prompt) and ``examples/``. The same folder works in Claude Code today and in
another engine later, unchanged.
"""

from shotforge.skills.registry import Skill, SkillError, bundled_root, find_skill, list_skills
from shotforge.skills.render import (
    latest_prompt,
    render_prompt,
    save_prompt,
    shot_context,
)

__all__ = [
    "Skill",
    "SkillError",
    "bundled_root",
    "find_skill",
    "latest_prompt",
    "list_skills",
    "render_prompt",
    "save_prompt",
    "shot_context",
]
