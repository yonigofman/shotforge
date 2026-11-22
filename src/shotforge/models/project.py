"""``project.yaml``: the few facts every prompt and every render needs."""

from __future__ import annotations

from pydantic import field_validator

from shotforge.models.common import ASPECT_RE, SFModel


class ToolPaths(SFModel):
    """Overrides for the executables Shotforge looks for (auto-detected otherwise)."""

    blender: str | None = None
    ffmpeg: str | None = None
    ffprobe: str | None = None
    claude: str | None = None


class ProjectConfig(SFModel):
    name: str
    language: str = "ru"
    aspect_ratio: str = "16:9"
    fps: int = 24
    #: Adapters in play, in the order the cockpit offers them.
    tools: list[str] = ["chatgpt", "grok"]
    #: Where image and video takes live relative to Git: lfs | external | git.
    media: str = "lfs"
    engine: str = "claude-code"
    tool_paths: ToolPaths = ToolPaths()

    @field_validator("aspect_ratio")
    @classmethod
    def _check_aspect(cls, v: str) -> str:
        if not ASPECT_RE.match(v):
            raise ValueError(f"aspect_ratio {v!r} must look like 16:9")
        return v

    @field_validator("fps")
    @classmethod
    def _check_fps(cls, v: int) -> int:
        if not 1 <= v <= 120:
            raise ValueError("fps must be between 1 and 120")
        return v

    @property
    def aspect_tuple(self) -> tuple[int, int]:
        w, h = self.aspect_ratio.split(":")
        return int(w), int(h)
