"""App wiring: FastAPI (REST + static PWA) with the MCP server mounted at /mcp."""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .config import Settings
from .db import make_engine, new_session
from .errors import Ambiguous, DomainError
from .mcp_server import build_mcp
from .models import utcnow
from .presets import load_presets_if_empty
from .service import Tracker

log = logging.getLogger("practice_tracker")
STATIC = Path(__file__).parent / "static"
OPEN_PATHS = ("/api/health",)


def token_name(settings: Settings, header: str | None) -> str | None:
    if not header or not header.lower().startswith("bearer "):
        return None
    given = header[7:].strip()
    for token, name in settings.tokens.items():
        if hmac.compare_digest(token, given):
            return name
    return None


class BearerAuth:
    """Pure ASGI middleware: /api/* and /mcp* need a valid token; the PWA shell is public."""

    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if path == "/mcp":  # serve /mcp directly instead of redirecting to /mcp/
            scope = {**scope, "path": "/mcp/", "raw_path": b"/mcp/"}
            path = "/mcp/"
        needs_auth = scope["type"] == "http" and (path.startswith("/api/") or path.startswith("/mcp"))
        if needs_auth and path not in OPEN_PATHS:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            name = token_name(self.settings, headers.get("authorization"))
            if name is None:
                response = JSONResponse({"detail": "missing or invalid bearer token"}, status_code=401,
                                        headers={"WWW-Authenticate": "Bearer"})
                await response(scope, receive, send)
                return
            scope.setdefault("state", {})["token_name"] = name
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None, clock: Callable[[], datetime] = utcnow) -> FastAPI:
    settings = settings or Settings.from_env()
    if not settings.tokens:
        raise SystemExit("PT_TOKENS is empty. Set e.g. PT_TOKENS='web:<random>,ha:<random>,agent:<random>'")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_url)
    with new_session(engine) as db:
        loaded = load_presets_if_empty(db, settings.presets_dir)
        if loaded:
            log.info("loaded presets: %s", ", ".join(loaded))

    def make_tracker() -> Tracker:
        return Tracker(new_session(engine), settings, clock)

    mcp = build_mcp(make_tracker)
    mcp_app = mcp.streamable_http_app(streamable_http_path="/", stateless_http=True, json_response=True,
                                      host="0.0.0.0")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(title="Practice Tracker", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)
    app.state.settings = settings
    app.state.make_tracker = make_tracker

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError):
        body = {"detail": str(exc)}
        if isinstance(exc, Ambiguous):
            body["candidates"] = exc.candidates
        return JSONResponse(body, status_code=exc.status)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    app.include_router(router)
    app.mount("/mcp", mcp_app)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():  # served from the root so its scope covers the whole app
        return FileResponse(STATIC / "sw.js", media_type="text/javascript")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

    app.add_middleware(BearerAuth, settings=settings)
    return app


def main() -> None:
    import os

    import uvicorn

    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=os.environ.get("PT_HOST", "0.0.0.0"), port=int(os.environ.get("PT_PORT", "8000")),
                proxy_headers=True)


if __name__ == "__main__":
    main()
