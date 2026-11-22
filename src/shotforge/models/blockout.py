"""Optional measured geometry for a location and its 180-degree line."""

from math import hypot
from typing import Annotated, Literal

from pydantic import Field, FiniteFloat, model_validator

from shotforge.models.common import SFModel

Point = tuple[FiniteFloat, FiniteFloat]
Length = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class BlockoutWindow(SFModel):
    wall: Literal["back", "front", "left", "right"] = "back"
    offset: FiniteFloat = 0
    width: Length = 1.8
    height: Length = 2.05
    sill: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.8


class BlockoutTable(SFModel):
    center: Point = (0.0, 0.0)
    size: tuple[Length, Length, Length] = (2.7, 0.82, 1.0)


class BlockoutSet(SFModel):
    room: tuple[Length, Length, Length] = (7.0, 5.0, 3.2)
    window: BlockoutWindow | Literal[False] = Field(default_factory=BlockoutWindow)
    hero_table: BlockoutTable = Field(default_factory=BlockoutTable)

    @model_validator(mode="after")
    def _check_bounds(self) -> "BlockoutSet":
        width, depth, height = self.room
        if self.window:
            window = self.window
            span = width if window.wall in ("back", "front") else depth
            if (
                abs(window.offset) + window.width / 2 > span / 2
                or window.sill + window.height > height
            ):
                raise ValueError("blockout window must fit within its wall")
        x, y = self.hero_table.center
        length, breadth, top = self.hero_table.size
        if abs(x) + length / 2 > width / 2 or abs(y) + breadth / 2 > depth / 2 or top > height:
            raise ValueError("blockout hero_table must fit within the room")
        return self


def signed_distance(start: Point, end: Point, point: Point) -> float:
    """Signed distance to the directed XY line (metres)."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    return (dx * (point[1] - start[1]) - dy * (point[0] - start[0])) / hypot(dx, dy)


class BlockoutAxis(SFModel):
    start: Point
    end: Point
    room_side: Point

    @model_validator(mode="after")
    def _check_line(self) -> "BlockoutAxis":
        if self.start == self.end:
            raise ValueError("axis_180 start and end must be distinct")
        if abs(signed_distance(self.start, self.end, self.room_side)) <= 1e-6:
            raise ValueError("axis_180 room_side must be off the line")
        return self
