"""The one interface the pipeline knows."""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shotforge.skills.registry import Skill


class EngineError(Exception):
    pass


@dataclass
class EngineResult:
    """What a skill run produced."""

    text: str
    data: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    cost_note: str | None = None

    def structured(self) -> Any:
        """Read a text skill's JSON output, including a reported engine or skill error."""
        if self.data.get("is_error"):
            raise EngineError(self.text[:500])
        payload = self.data.get("parsed")
        if payload is None:
            payload = json.loads(self.text)
        if isinstance(payload, dict) and isinstance(error := payload.get("error"), str):
            raise EngineError(error[:500])
        return payload


class Engine(abc.ABC):
    """Runs a skill on some backend. The pipeline only ever sees this."""

    name: str = "engine"

    @abc.abstractmethod
    def run_skill(
        self,
        skill: Skill,
        inputs: dict[str, Any],
        *,
        cwd: Path | None = None,
        files: list[Path] | None = None,
    ) -> EngineResult:
        """Run ``skill`` over ``inputs`` and return its output."""

    def available(self) -> bool:
        return True


def get_engine(name: str) -> Engine:
    """``claude-code`` today; the name is the seam for what comes next."""
    from shotforge.engine.claude_code import ClaudeCodeEngine
    from shotforge.engine.template import TemplateEngine

    engines: dict[str, type[Engine]] = {
        "claude-code": ClaudeCodeEngine,
        "template": TemplateEngine,
    }
    if name not in engines:
        raise EngineError(f"unknown engine {name!r} (have: {', '.join(sorted(engines))})")
    return engines[name]()
