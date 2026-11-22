"""Project-local options for Claude Code."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from shotforge.models.common import SFModel
from shotforge.yamlio import ProjectFileError, load

Effort = Literal["low", "medium", "high", "xhigh", "max"]
ModelName = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/@+\[\]-]*$", max_length=200)
]


class ClaudeSettings(SFModel):
    model: ModelName | None = None
    effort: Effort | None = None
    fallback_models: list[ModelName] = Field(default_factory=list)
    timeout_s: int | None = Field(default=None, ge=1, le=86400)


def read_claude_settings(path: Path) -> ClaudeSettings:
    try:
        return load(path, ClaudeSettings)
    except FileNotFoundError:
        return ClaudeSettings()
    except OSError as exc:
        raise ProjectFileError(path, str(exc)) from exc
