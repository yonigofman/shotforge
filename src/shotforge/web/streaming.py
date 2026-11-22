"""Stream action progress and finish with server-rendered HTML."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from threading import Event
from typing import Any

from fastapi import HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from shotforge.engine import EngineCancelled, EngineProgress

MEDIA_TYPE = "application/x-ndjson"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamProgress:
    send: Callable[[dict[str, Any]], None]
    cancelled: Event

    def status(self, message: str) -> None:
        self.send({"type": "status", "message": message})

    def engine(self, message: str) -> EngineProgress:
        return EngineProgress(lambda: self.status(message), self.cancelled, self.status)


async def render_with_progress(
    request: Request, generate: Callable[[StreamProgress | None], HTMLResponse],
) -> Response:
    accepts_stream = any(
        item.strip().split(";", 1)[0] == MEDIA_TYPE
        for item in request.headers.get("accept", "").split(",")
    )
    if accepts_stream:
        return _stream_html(generate)
    return await run_in_threadpool(generate, None)


def _stream_html(generate: Callable[[StreamProgress], HTMLResponse]) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        pending: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        cancelled = Event()

        def send(event: dict[str, Any]) -> None:
            if not cancelled.is_set():
                loop.call_soon_threadsafe(pending.put_nowait, event)

        async def run() -> None:
            try:
                response = await run_in_threadpool(generate, StreamProgress(send, cancelled))
            except HTTPException as exc:
                send({"type": "error", "message": str(exc.detail)})
            except EngineCancelled as exc:
                send({"type": "error", "message": str(exc)})
            except Exception:
                logger.exception("Streamed cockpit action failed")
                send({"type": "error", "message": "Could not complete the request. Try again."})
            else:
                result = {"type": "complete", "html": response.body.decode("utf-8")}
                if trigger := response.headers.get("HX-Trigger-After-Swap"):
                    result["trigger"] = trigger
                send(result)

        task = asyncio.create_task(run())
        try:
            while True:
                try:
                    event = await asyncio.wait_for(pending.get(), timeout=10)
                except TimeoutError:
                    # Keep the connection open and detect a disconnected browser
                    # even while waiting for the first model output.
                    yield "\n"
                    continue
                yield json.dumps(event, ensure_ascii=False) + "\n"
                if event["type"] in {"complete", "error"}:
                    break
        finally:
            cancelled.set()
            task.cancel()

    return StreamingResponse(events(), media_type=MEDIA_TYPE, headers={"Cache-Control": "no-store"})
