"""The shot spec — the heart of the system.

One ``shot.yaml`` carries everything a prompt, a QA pass and the next shot
need: camera, who is in frame, the beats with their timecodes, and the
continuity state the shot comes in with and goes out on.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AliasChoices, Field, FiniteFloat, field_validator, model_validator

from shotforge.models.blockout import Point
from shotforge.models.common import ID_RE, WORDS_PER_SECOND, SFModel, word_count


class Stage(StrEnum):
    """The five stages of a shot. Every stage closes on your approval."""

    breakdown = "breakdown"
    blockout = "blockout"
    frame = "frame"
    video = "video"
    done = "done"


class ShotStatus(StrEnum):
    """Where a shot stands. ``*_approved`` is only ever set by an approval gate."""

    breakdown = "breakdown"
    breakdown_approved = "breakdown_approved"
    blockout = "blockout"
    blockout_approved = "blockout_approved"
    frame = "frame"
    frame_approved = "frame_approved"
    video = "video"
    video_approved = "video_approved"
    done = "done"

    @property
    def stage(self) -> Stage:
        return Stage(self.value.replace("_approved", ""))

    @property
    def is_approved(self) -> bool:
        return self.value.endswith("_approved") or self is ShotStatus.done


class Camera(SFModel):
    blender_cam: str | None = None
    lens_mm: int | None = None
    position: str | Point | None = None
    height: str | FiniteFloat | None = None
    angle: str | FiniteFloat | None = None
    move: str = "static"


class FramedCharacter(SFModel):
    """A character visible in the shot, with the frame-relative facts a prompt needs."""

    char: str
    role: str = "subject"
    view: str | None = None
    frame_x: str | None = None
    eyeline: str | None = None
    notes: str | None = None


class Beat(SFModel):
    """A slice of the shot's timeline: an action, a line, or a deliberate silence."""

    t: tuple[FiniteFloat, FiniteFloat]
    action: str | None = None
    speaker: str | None = None
    line: str | None = None
    silence: bool = False

    @property
    def duration_s(self) -> float:
        return self.t[1] - self.t[0]

    @model_validator(mode="after")
    def _check(self) -> Beat:
        if self.t[1] <= self.t[0]:
            raise ValueError(f"beat {self.t} ends before it starts")
        if self.silence and self.speaker:
            raise ValueError(
                f"beat {self.t} is marked silence: true but has speaker {self.speaker!r} "
                "— nobody speaks during a silent beat"
            )
        if self.line and not self.speaker:
            raise ValueError(f"beat {self.t} has a line but no speaker")
        if not (self.action or self.line or self.silence):
            raise ValueError(f"beat {self.t} is empty: give it an action, a line or silence: true")
        return self

    def line_overruns(self) -> float:
        """Seconds by which the line is too long for the beat (0 when it fits)."""
        if not self.line:
            return 0.0
        needed = word_count(self.line) / WORDS_PER_SECOND
        return max(0.0, round(needed - self.duration_s, 2))


class Continuity(SFModel):
    """Free-form state keys (beer, durik_hand, mood) read at the cut points.

    ``in_`` is spelled ``in`` in YAML, where it reads naturally beside ``out``.
    """

    in_: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("in", "in_"),
        serialization_alias="in",
    )
    out: dict[str, str] = Field(default_factory=dict)


class Refs(SFModel):
    from_shots: list[str] = []
    face_crops: list[str] = []
    extra: list[str] = []


class Sound(SFModel):
    ambience: str | None = None
    music: str | None = None
    notes: str | None = None


class Shot(SFModel):
    id: str
    duration_s: FiniteFloat
    size: str | None = None
    summary: str | None = None
    camera: Camera = Camera()
    technique: list[str] = []
    in_frame: list[FramedCharacter] = []
    beats: list[Beat] = []
    continuity: Continuity = Continuity()
    refs: Refs = Refs()
    sound: Sound = Sound()
    status: ShotStatus = ShotStatus.breakdown

    @field_validator("id")
    @classmethod
    def _check_id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError(f"shot id {v!r} must be lowercase letters, digits and underscores")
        return v

    @field_validator("duration_s")
    @classmethod
    def _check_duration(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("duration_s must be positive")
        return v

    @model_validator(mode="after")
    def _check_beats(self) -> Shot:
        last = 0.0
        for beat in self.beats:
            if beat.t[0] < last:
                raise ValueError(f"beat {beat.t} overlaps the beat before it (ends at {last})")
            last = beat.t[1]
        if self.beats and last > self.duration_s + 0.01:
            raise ValueError(
                f"beats run to {last}s but the shot is {self.duration_s}s long"
            )
        return self

    @property
    def subject(self) -> FramedCharacter | None:
        for framed in self.in_frame:
            if framed.role == "subject":
                return framed
        return self.in_frame[0] if self.in_frame else None

    @property
    def characters(self) -> list[str]:
        return [f.char for f in self.in_frame]

    def speakers(self) -> list[str]:
        return [b.speaker for b in self.beats if b.speaker]
