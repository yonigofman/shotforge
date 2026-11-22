"""Restrict filesystem-facing cockpit requests to a local browser."""

from ipaddress import ip_address
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send


def is_loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return host is not None and ip_address(host).is_loopback
    except ValueError:
        return False


class LocalOnlyMiddleware:
    def __init__(self, app: ASGIApp, *, enforce_all: bool) -> None:
        self.app = app
        self.enforce_all = enforce_all

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            request = Request(scope)
            section = scope["path"].split("/", 2)[1]
            if (self.enforce_all or section in {"projects", "p"}) and not self.allowed(request):
                response = PlainTextResponse("The cockpit only accepts local requests.", 403)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    def allowed(request: Request) -> bool:
        host = request.headers.get("host", "")
        try:
            authority = urlsplit(f"//{host}")
            if authority.netloc != host or not is_loopback(authority.hostname):
                return False
            if authority.username or authority.password:
                return False
            if request.client is None or not is_loopback(request.client.host):
                return False
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("origin")
                if origin and origin != f"{request.scope['scheme']}://{host}":
                    return False
                if request.headers.get("sec-fetch-site") == "cross-site":
                    return False
        except ValueError:
            return False
        return True
