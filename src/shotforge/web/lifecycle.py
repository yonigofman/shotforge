"""Telling long-lived responses that the cockpit is shutting down.

Uvicorn waits for open connections before it exits, and the event stream a
cockpit tab holds never ends on its own. Without this, Ctrl+C printed
"Waiting for connections to close" and appeared to hang, then killed the
stream mid-flight and logged the traceback.

The server sets this flag the moment it catches the signal, so every stream
can finish its own response first and the shutdown stays quiet.
"""

from __future__ import annotations

from threading import Event

_shutting_down = Event()


def request_shutdown() -> None:
    """Called by the server when it catches SIGINT or SIGTERM."""
    _shutting_down.set()


def shutting_down() -> bool:
    return _shutting_down.is_set()


def reset() -> None:
    """For tests, and for a reloader that starts a fresh server in-process."""
    _shutting_down.clear()


def watch_for_shutdown(uvicorn) -> None:  # noqa: ANN001 - the uvicorn module
    """Have uvicorn raise the flag the moment it catches the signal.

    Uvicorn builds its own ``Server`` inside ``uvicorn.run``, so the hook goes
    on the class. Its own ``handle_exit`` still runs, unchanged and first in
    line of everything it already does.
    """
    reset()
    if getattr(uvicorn.Server.handle_exit, "_shotforge_hook", False):
        return
    original = uvicorn.Server.handle_exit

    def handle_exit(self, sig, frame):  # noqa: ANN001 - uvicorn's signature
        request_shutdown()
        original(self, sig, frame)

    handle_exit._shotforge_hook = True
    uvicorn.Server.handle_exit = handle_exit
