"""FastAPI application factory for the core-api (data-contract foundation)."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import init_db
from .routers import classes, counters, network, scenarios, seed


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Enable PostGIS + create tables/GiST indexes (retries while the DB warms up).
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="macromodel core-api",
        version="0.1.0",
        summary="Native-PostGIS data-contract foundation for the rebuilt macromodel.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz", tags=["meta"])
    def healthz():
        return {"status": "ok", "service": "core-api", "srid": settings.srid}

    app.include_router(scenarios.router)
    app.include_router(network.router)
    app.include_router(classes.router)
    app.include_router(seed.router)
    app.include_router(counters.router)
    return app


app = create_app()
