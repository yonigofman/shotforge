"""Stream proposal progress and finish with the server-rendered review."""

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
from fastapi.responses import HTMLResponse, StreamingResponse

from shotforge.engine import EngineProgress

MEDIA_TYPE = "application/x-ndjson"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProposalProgress:
    send: Callable[[dict[str, Any]], None]
    cancelled: Event

    def status(self, message: str) -> None:
        self.send({"type": "status", "message": message})

    def engine(self, message: str) -> EngineProgress:
        return EngineProgress(lambda: self.status(message), self.cancelled)


def accepts_proposal_stream(request: Request) -> bool:
    return any(
        item.strip().split(";", 1)[0] == MEDIA_TYPE
        for item in request.headers.get("accept", "").split(",")
    )


def stream_proposal(generate: Callable[[ProposalProgress], HTMLResponse]) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        pending: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        cancelled = Event()

        def send(event: dict[str, Any]) -> None:
            if not cancelled.is_set():
                loop.call_soon_threadsafe(pending.put_nowait, event)

        async def run() -> None:
            try:
                response = await run_in_threadpool(generate, ProposalProgress(send, cancelled))
            except HTTPException as exc:
                send({"type": "error", "message": str(exc.detail)})
            except Exception:
                logger.exception("Proposal generation failed")
                send({"type": "error", "message": "Proposal generation failed. Try again."})
            else:
                send({"type": "complete", "html": response.body.decode("utf-8")})

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
