"""An engine that only renders templates — useful offline and in tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shotforge.engine.base import Engine, EngineResult
from shotforge.skills.registry import Skill


class TemplateEngine(Engine):
    """Renders ``template.j2`` and returns it. No model, no network."""

    name = "template"

    def run_skill(
        self,
        skill: Skill,
        inputs: dict[str, Any],
        *,
        cwd: Path | None = None,
        files: list[Path] | None = None,
    ) -> EngineResult:
        from shotforge.skills.render import render_prompt

        text = render_prompt(skill, inputs)
        return EngineResult(text=text, data={}, raw=text)
