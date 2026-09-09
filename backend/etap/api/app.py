"""FastAPI application factory."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..db import create_all
from . import routes_attempts, routes_auth, routes_chat, routes_media, routes_papers

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    create_all()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="AptoriQ",
        version="0.1.0",
        description="Timed exam runtime with behavioural instrumentation.",
        lifespan=lifespan,
    )

    # On the classroom LAN the frontend is served from the same origin, but the Vite dev
    # server runs on its own port, so development needs the allowance.
    origins = DEV_ORIGINS + [
        origin.strip()
        for origin in os.getenv("ETAP_CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes_auth.router)
    app.include_router(routes_chat.router)
    app.include_router(routes_attempts.router)
    app.include_router(routes_papers.router)
    app.include_router(routes_media.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built frontend, if it has been built.

    Keeping it on the same origin means the classroom only needs one port open and no CORS
    configuration. During development the Vite server proxies to the API instead, so a
    missing `dist` directory is not an error.
    """
    if not FRONTEND_DIST.is_dir():
        return

    assets = FRONTEND_DIST / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = FRONTEND_DIST / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def serve_spa(path: str) -> FileResponse:
        # Unknown /api paths must still 404 rather than being handed the HTML shell.
        if path.startswith("api/"):
            raise HTTPException(404, "Not found.")

        candidate = (FRONTEND_DIST / path).resolve()
        if (
            path
            and candidate.is_file()
            and candidate.is_relative_to(FRONTEND_DIST.resolve())
        ):
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
