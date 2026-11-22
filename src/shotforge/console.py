"""Terminal output is UTF-8, on every system.

A project's names, dialogue and notes are Russian and Hebrew. The Windows
console still defaults to a legacy code page (cp1252 on an English install),
where printing a Cyrillic project name raises ``UnicodeEncodeError`` and kills
the command. Every entry point reconfigures its streams first.
"""

from __future__ import annotations

import sys
from typing import TextIO

UTF8_NAMES = {"utf-8", "utf8", "utf_8"}


def force_utf8(stream: TextIO) -> bool:
    """Switch one text stream to UTF-8. True when it changed."""
    encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "_")
    if encoding in {name.replace("-", "_") for name in UTF8_NAMES}:
        return False
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return False
    try:
        # errors="replace" so an unprintable glyph never costs a command.
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        return False
    return True


def setup_console() -> None:
    """Called by the CLI and the server before anything is printed."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None:
            force_utf8(stream)
