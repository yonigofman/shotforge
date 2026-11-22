"""The engine layer.

The backend never calls Claude Code directly. It calls one interface —
``Engine.run_skill(skill, inputs) -> outputs`` — so another engine can be
added later without touching the pipeline or the skills.
"""

from shotforge.engine.base import Engine, EngineError, EngineResult, get_engine
from shotforge.engine.claude_code import ClaudeCodeEngine
from shotforge.engine.template import TemplateEngine

__all__ = [
    "ClaudeCodeEngine",
    "Engine",
    "EngineError",
    "EngineResult",
    "TemplateEngine",
    "get_engine",
]
