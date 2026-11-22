"""Finding Blender, ffmpeg and Claude Code on Mac, Windows and Linux.

Standard install locations per system, plus an override in ``project.yaml``
(``tool_paths:``). ``shotforge doctor`` reports what is missing and what to do
about it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Overrides from the open project's ``tool_paths``.
_OVERRIDES: dict[str, str] = {}

CANDIDATES: dict[str, dict[str, tuple[str, ...]]] = {
    "blender": {
        "darwin": ("/Applications/Blender.app/Contents/MacOS/Blender",),
        "win32": (
            r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.1\blender.exe",
            r"C:\Program Files\Blender Foundation\Blender 4.0\blender.exe",
        ),
        "linux": ("/usr/bin/blender", "/snap/bin/blender"),
    },
    "ffmpeg": {
        "darwin": ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"),
        "win32": (r"C:\ffmpeg\bin\ffmpeg.exe",),
        "linux": ("/usr/bin/ffmpeg",),
    },
    "ffprobe": {
        "darwin": ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe"),
        "win32": (r"C:\ffmpeg\bin\ffprobe.exe",),
        "linux": ("/usr/bin/ffprobe",),
    },
    "claude": {
        "darwin": ("~/.claude/local/claude", "/opt/homebrew/bin/claude"),
        "win32": (r"%LOCALAPPDATA%\Programs\claude\claude.exe",),
        "linux": ("~/.claude/local/claude", "~/.local/bin/claude"),
    },
}

HINTS = {
    "blender": "Install Blender from blender.org, or set tool_paths.blender in project.yaml.",
    "ffmpeg": "Install ffmpeg (brew install ffmpeg / winget install Gyan.FFmpeg).",
    "ffprobe": "Comes with ffmpeg; install ffmpeg to get it.",
    "claude": "Install Claude Code (claude.ai/code) and sign in with your subscription.",
}

REQUIRED = ("ffmpeg", "ffprobe")
OPTIONAL = ("blender", "claude")


class ToolError(Exception):
    """A tool the step needs is not installed."""


def configure(overrides: dict[str, str | None] | None) -> None:
    """Register the open project's ``tool_paths`` overrides."""
    _OVERRIDES.clear()
    for name, value in (overrides or {}).items():
        if value:
            _OVERRIDES[name] = value


def _platform_key() -> str:
    if sys.platform.startswith("win"):
        return "win32"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def find_tool(name: str) -> Path | None:
    """The override, then PATH, then the standard locations for this system."""
    override = _OVERRIDES.get(name) or os.environ.get(f"SHOTFORGE_{name.upper()}")
    if override:
        candidate = Path(os.path.expandvars(override)).expanduser()
        return candidate if candidate.exists() else None
    on_path = shutil.which(name)
    if on_path:
        return Path(on_path)
    for raw in CANDIDATES.get(name, {}).get(_platform_key(), ()):
        candidate = Path(os.path.expandvars(raw)).expanduser()
        if candidate.exists():
            return candidate
    return None


def require_tool(name: str) -> Path:
    found = find_tool(name)
    if found is None:
        raise ToolError(f"{name} not found. {HINTS.get(name, '')}".strip())
    return found


@dataclass
class ToolStatus:
    name: str
    path: Path | None
    version: str | None
    required: bool

    @property
    def ok(self) -> bool:
        return self.path is not None

    @property
    def hint(self) -> str:
        return "" if self.ok else HINTS.get(self.name, "")


def _version(name: str, path: Path) -> str | None:
    flag = "--version"
    try:
        result = subprocess.run(
            [str(path), flag], capture_output=True, text=True, timeout=20
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = (result.stdout or result.stderr).strip().splitlines()
    return text[0][:80] if text else None


def doctor() -> list[ToolStatus]:
    """What is installed, what is missing, and what each one is needed for."""
    statuses = []
    for name in (*REQUIRED, *OPTIONAL):
        path = find_tool(name)
        statuses.append(
            ToolStatus(
                name=name,
                path=path,
                version=_version(name, path) if path else None,
                required=name in REQUIRED,
            )
        )
    return statuses
