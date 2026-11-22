"""Shared building blocks for every Shotforge schema.

Every YAML file in a project is validated against these models before it is
written, so a malformed file never reaches the pipeline.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_]*$")
ASPECT_RE = re.compile(r"^\d{1,2}:\d{1,2}$")
#: Rough speaking rate used to flag dialogue that cannot fit its beat.
WORDS_PER_SECOND = 2.5


class SFModel(BaseModel):
    """Base model: unknown keys are rejected so typos surface immediately."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)


class Entity(SFModel):
    """Anything in the bible that a prompt can refer to by id or @tag."""

    id: str
    tag: str | None = None
    names: dict[str, str] = {}
    from_book: list[str] = []
    design_choices: list[str] = []
    assets: dict[str, str] = {}

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"id {v!r} must be lowercase letters, digits and underscores")
        return v

    @field_validator("tag")
    @classmethod
    def _check_tag(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("@"):
            raise ValueError(f"tag {v!r} must start with '@' (it is pasted into Grok prompts)")
        return v


def word_count(line: str) -> int:
    return len([w for w in re.split(r"\s+", line.strip()) if w])


def as_plain(data: Any) -> Any:
    """Recursively turn models into plain data ready for YAML."""
    if isinstance(data, BaseModel):
        return as_plain(data.model_dump(mode="json", exclude_none=True, by_alias=True))
    if isinstance(data, dict):
        return {k: as_plain(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [as_plain(v) for v in data]
    return data
