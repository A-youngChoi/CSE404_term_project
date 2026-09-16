"""PaperCue local backend entry point.

Run with `python -m app.main` (binds to PAPERCUE_HOST, default 127.0.0.1).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import routes_papers, routes_sessions, routes_system
from app.core.config import Settings, get_settings
from app.core.errors import PaperCueError
from app.core.logging import configure_logging, get_logger
from app.services.container import Container

log = get_logger("main")

API_PREFIX = "/api/v1"
DASHBOARD_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
DOC_PATHS = ("/docs", "/openapi.json")


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.purge_expired_on_startup:
            result = app.state.container.sessions.purge_expired()
            log.info("retention purge on startup removed %d session(s)", result["purged_sessions"])
        yield
        app.state.container.close()

    app = FastAPI(
        title="PaperCue local backend",
        version="0.2.0",
        description=(
            "Local-only research prototype. Consented research testing only - not covert listening. "
            "No remote AI, embedding, analytics or telemetry services are used."
        ),
        docs_url="/docs" if settings.api_docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.container = container or Container(settings)

    # Only explicit loopback origins (validated in Settings); never a wildcard.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "microphone=(), camera=(), geolocation=()"
        if not request.url.path.startswith(DOC_PATHS):
            response.headers["Content-Security-Policy"] = DASHBOARD_CSP
        if request.url.path.startswith(API_PREFIX):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(PaperCueError)
    async def papercue_error(_: Request, exc: PaperCueError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        # Do not echo submitted values (they may contain conversation text).
        errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"error": {"code": "invalid_request", "message":
                                                                 "Request validation failed.", "details": errors}})

    app.include_router(routes_system.router, prefix=API_PREFIX)
    app.include_router(routes_papers.router, prefix=API_PREFIX)
    app.include_router(routes_sessions.router, prefix=API_PREFIX)

    dist = Path(settings.dashboard_dist_dir)
    if (dist / "index.html").is_file():
        app.mount("/dashboard", StaticFiles(directory=dist, html=True), name="dashboard")

    @app.get("/", include_in_schema=False)
    def root():
        if (dist / "index.html").is_file():
            return RedirectResponse("/dashboard/")
        return RedirectResponse("/docs" if settings.api_docs_enabled else f"{API_PREFIX}/health")

    return app


def run() -> None:
    import uvicorn

    settings = get_settings()
    log.info("starting PaperCue on %s:%d (local only)", settings.papercue_host, settings.papercue_port)
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.papercue_host,
        port=settings.papercue_port,
        server_header=False,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
