"""Application factory: wires configuration, database, engine, routes and static files."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__, auth, routes, ws
from .config import WEB_DIR, Settings, load_settings
from .db import Database
from .engine import EngineService
from .manager import GameManager
from .security import hash_password

log = logging.getLogger("lanchess")


def _ensure_admin(db: Database, settings: Settings) -> None:
    """Create (or update) the administrator account defined in the environment."""
    if not settings.admin_username or not settings.admin_password:
        return
    password_hash = hash_password(settings.admin_password)
    existing = db.user_by_name(settings.admin_username)
    if existing is None:
        db.create_user(settings.admin_username, password_hash, is_admin=True)
        log.info("Created administrator account '%s'", settings.admin_username)
    else:
        db.set_password(existing["id"], password_hash, is_admin=True)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(
        level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.db_path)
        db.purge_expired_sessions()
        _ensure_admin(db, settings)
        engine = EngineService(
            settings.stockfish_path,
            threads=settings.engine_threads,
            hash_mb=settings.engine_hash_mb,
            max_analysis=settings.max_analysis_engines,
        )
        manager = GameManager(db, engine)
        app.state.settings = settings
        app.state.db = db
        app.state.engine = engine
        app.state.manager = manager
        manager.start()
        if engine.available:
            log.info("Stockfish found at %s", settings.stockfish_path)
        else:
            log.warning("Stockfish not found - play vs computer and analysis are disabled")
        try:
            yield
        finally:
            await manager.stop()
            await engine.close()
            db.close()

    app = FastAPI(
        title=settings.site_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.include_router(auth.router)
    app.include_router(routes.router)
    app.include_router(ws.router)
    # The single-page frontend; mounted last so /api and /ws take precedence.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
