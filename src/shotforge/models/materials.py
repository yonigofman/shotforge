"""Texture assignments for the optional Blender material pass."""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, RootModel

from shotforge.models.common import ID_RE, SFModel

_SURFACES = {"location": ("wall", "floor", "table"), "character": ("garment",)}
_PNG_NAME = re.compile(r"[a-z0-9][a-z0-9_]*\.png")


def surface_name(value: str) -> str:
    parts = value.split(".")
    if (
        len(parts) != 3
        or not ID_RE.fullmatch(parts[1])
        or parts[2] not in _SURFACES.get(parts[0], ())
    ):
        raise ValueError("use location.<id>.<wall|floor|table> or character.<id>.garment")
    return value


def texture_filename(value: str) -> str:
    if not _PNG_NAME.fullmatch(value):
        raise ValueError("use a PNG filename with lowercase letters, digits and underscores")
    return value


class SheetSource(SFModel):
    kind: Literal["character", "location", "prop"]
    id: Annotated[str, Field(pattern=ID_RE.pattern)]


class Material(SFModel):
    texture: Annotated[str, AfterValidator(texture_filename)]
    uv_scale: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 1
    source: SheetSource | None = None


class Materials(RootModel[dict[Annotated[str, AfterValidator(surface_name)], Material]]):
    def source_for(self, surface: str) -> SheetSource:
        if source := self.root[surface].source:
            return source
        kind, entity_id, _ = surface.split(".")
        return SheetSource(kind=kind, id=entity_id)
