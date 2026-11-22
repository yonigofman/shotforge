"""A scene: what happens, where, and where everyone sits."""

from __future__ import annotations

from pydantic import field_validator

from shotforge.models.blockout import BlockoutAxis
from shotforge.models.common import ID_RE, SFModel


class Seat(SFModel):
    """Seating is fixed for the whole scene; every shot is checked against it."""

    char: str
    seat: str
    faces: str | None = None


class Scene(SFModel):
    id: str
    location: str
    time: str | None = None
    summary: str | None = None
    characters: list[str] = []
    seating: list[Seat] = []
    axis_180: str | BlockoutAxis | None = None
    lighting: str | None = None
    sound: dict[str, str] = {}

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"scene id {v!r} must be lowercase letters, digits and underscores")
        return v

    def seat_of(self, char: str) -> Seat | None:
        for seat in self.seating:
            if seat.char == char:
                return seat
        return None
