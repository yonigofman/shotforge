"""The bible: the single source of truth for the world.

``style.yaml`` plus one file per character, location and prop. Prompts are
derived from these files; they are never written by hand.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field

from shotforge.models.blockout import BlockoutSet
from shotforge.models.common import Entity, SFModel


class ContinuityArc(SFModel):
    """An attribute that drifts on purpose across a scene (e.g. a flushing face)."""

    attr: str
    start: str = Field(validation_alias=AliasChoices("from", "start"), serialization_alias="from")
    to: str
    over: str


class Style(SFModel):
    """Art style, film stock, palette and world rules — one file per project."""

    art_style: str
    film_stock: str | None = None
    palette: list[str] = []
    grade: str | None = None
    world_rules: list[str] = []
    negative: list[str] = []


class Character(Entity):
    outfit: dict[str, str] = {}
    voice: str | None = None
    continuity_arcs: list[ContinuityArc] = []


class Location(Entity):
    blockout: BlockoutSet | None = None
    set_dressing: list[str] = []
    fixed_props: list[str] = []
    lighting: str | None = None


class Prop(Entity):
    description: str | None = None
    states: list[str] = []
