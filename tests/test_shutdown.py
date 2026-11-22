"""The cockpit must exit when you ask it to.

An open tab holds the event stream, and uvicorn waits for open connections
before it exits. When the stream never ended, Ctrl+C printed "Waiting for
connections to close" and looked like a hang, then killed the stream in
flight and logged a CancelledError traceback.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from shotforge.web import app as web_app
from shotforge.web import lifecycle
from shotforge.web.app import create_app


@pytest.fixture(autouse=True)
def quiet_lifecycle():
    lifecycle.reset()
    yield
    lifecycle.reset()


@pytest.fixture
def client(project):
    return TestClient(create_app(project.paths.root))


def test_the_event_stream_ends_on_its_own(client, monkeypatch):
    """Otherwise one tab is enough to hold the server open for good."""
    monkeypatch.setattr(web_app, "EVENT_STREAM_SECONDS", 0.2)
    monkeypatch.setattr(web_app, "EVENT_POLL_SECONDS", 0.05)
    response = client.get("/events")
    assert response.status_code == 200
    assert response.text.count("data:") >= 1


def test_the_stream_stops_as_soon_as_a_shutdown_is_asked_for(client, monkeypatch):
    monkeypatch.setattr(web_app, "EVENT_STREAM_SECONDS", 30.0)
    monkeypatch.setattr(web_app, "EVENT_POLL_SECONDS", 10.0)
    lifecycle.request_shutdown()
    response = client.get("/events")
    assert response.status_code == 200
    assert response.text == ""


def test_the_flag_is_off_until_the_server_catches_a_signal():
    assert not lifecycle.shutting_down()
    lifecycle.request_shutdown()
    assert lifecycle.shutting_down()
    lifecycle.reset()
    assert not lifecycle.shutting_down()


def test_serve_hooks_the_signal_so_the_streams_hear_it(monkeypatch, project):
    """`serve` must arrange for a signal to raise the flag, not just run."""
    import uvicorn
    from typer.testing import CliRunner

    from shotforge.cli import app as cli_app

    started: dict[str, object] = {}
    monkeypatch.setattr(uvicorn, "run", lambda asgi, **kwargs: started.update(app=asgi, **kwargs))
    monkeypatch.setattr(uvicorn.Server, "handle_exit", uvicorn.Server.handle_exit)

    result = CliRunner().invoke(
        cli_app, ["serve", "-C", str(project.paths.root), "--port", "8799"]
    )
    assert result.exit_code == 0, result.output
    assert started["host"] == "127.0.0.1"
    assert started["timeout_graceful_shutdown"] == 5

    server = uvicorn.Server(uvicorn.Config(started["app"]))
    assert not lifecycle.shutting_down()
    server.handle_exit(2, None)
    assert lifecycle.shutting_down()
