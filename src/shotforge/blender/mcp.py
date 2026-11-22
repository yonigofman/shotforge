"""Send a render job to the local Blender MCP add-on."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from shotforge.blender.errors import BlockoutError


def _request(connection: socket.socket, command: dict) -> None:
    connection.sendall(json.dumps(command).encode("utf-8"))
    response = bytearray()
    while chunk := connection.recv(8192):
        response.extend(chunk)
        if len(response) > 1024 * 1024:
            raise BlockoutError("response from Blender MCP is too large")
        try:
            message = json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(message, dict) or message.get("status") != "success":
            detail = message.get("message") if isinstance(message, dict) else None
            raise BlockoutError(detail or "Blender MCP returned an error")
        return
    raise BlockoutError("Blender MCP closed the connection before replying")


def render(script: Path, job: Path, timeout: float) -> None:
    code = f"import runpy\nrunpy.run_path({str(script)!r})['render_job']({str(job)!r})"
    try:
        port = int(os.environ.get("SHOTFORGE_BLENDER_MCP_PORT", "9876"))
        if not 1 <= port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        with socket.create_connection(("127.0.0.1", port), timeout=2) as connection:
            _request(connection, {"type": "get_scene_info", "params": {}})
            connection.settimeout(timeout)
            _request(connection, {"type": "execute_code", "params": {"code": code}})
    except (OSError, ValueError) as exc:
        raise BlockoutError(str(exc)) from exc
