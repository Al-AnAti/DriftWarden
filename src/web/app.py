"""DriftWarden control plane: FastAPI application and engine lifespan.

The reconciliation engine runs cooperatively inside this process. The
lifespan wires settings, SQLite, the Docker watcher, the GitHub client and
the reconciler together, publishes them on ``app.state.warden`` and tears
everything down on shutdown.
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from shared.exceptions import DriftWardenError
from warden.automation.github_client import GitHubClient
from warden.config import WardenSettings, get_settings
from warden.core.docker_watcher import DockerWatcher
from warden.core.host_identity import resolve_host_identity
from warden.engine.reconciler import Reconciler
from warden.engine.state_projector import StateProjector
from web.db.repository import Store
from web.db.session import Database
from web.routes import api_actions, views
from web.runtime import WardenRuntime

STATIC_DIR = Path(__file__).parent / "static"


def configure_logging(settings: WardenSettings) -> None:
    """Route Loguru to stderr at the configured level."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
        ),
        enqueue=True,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start the engine before serving requests and stop it afterwards."""
    settings = get_settings()
    configure_logging(settings)
    logger.info("Starting DriftWarden control plane: {}", settings)

    watcher = DockerWatcher(settings)
    hostname, source = await asyncio.to_thread(
        resolve_host_identity, settings.warden_hostname, watcher.daemon_info
    )
    settings.warden_hostname = hostname
    logger.info("Host identity: {} (from {})", hostname, source)

    database = Database(settings.database_url)
    database.init()
    store = Store(database, settings.hostname)
    projector = StateProjector(settings.hostname, settings.warden_mode)
    github = None if settings.allow_mock_baseline else GitHubClient(settings)
    reconciler = Reconciler(settings, watcher, store, projector, github)
    app.state.warden = WardenRuntime(
        settings=settings,
        database=database,
        store=store,
        projector=projector,
        reconciler=reconciler,
    )
    await reconciler.start()
    try:
        yield
    finally:
        await reconciler.stop()
        database.close()
        logger.info("DriftWarden control plane stopped")


def create_app() -> FastAPI:
    """Build the FastAPI application.

    Returns:
        The configured application with routes, static files and handlers.
    """
    application = FastAPI(
        title="DriftWarden Control Plane",
        description="GitOps drift detection and remediation for standalone Docker hosts.",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    application.include_router(views.router)
    application.include_router(api_actions.router)

    @application.exception_handler(DriftWardenError)
    async def handle_warden_error(_: Request, exc: DriftWardenError) -> JSONResponse:
        logger.warning("Request failed: {}", exc)
        return JSONResponse(
            status_code=409,
            content={"ok": False, "error": exc.__class__.__name__, "message": str(exc)},
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        """Unexpected failures (Docker SDK, GitHub, bugs) reach the UI as a readable banner."""
        logger.exception("Unhandled error on {} {}: {}", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": exc.__class__.__name__,
                "message": f"{exc.__class__.__name__}: {exc}",
            },
        )

    return application


app = create_app()
