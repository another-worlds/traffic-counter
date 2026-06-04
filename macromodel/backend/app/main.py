from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import init_db
from .routers import (
    calibration, counters, crud, matrices, model, network, network_edit, results, scenarios, zones,
)
from .routers import procedures as procedures_router


def _init_db_with_retry(attempts: int = 10, delay: float = 2.0) -> None:
    last = None
    for _ in range(attempts):
        try:
            init_db()
            return
        except Exception as e:  # noqa: BLE001 — DB may still be starting
            last = e
            time.sleep(delay)
    if last:
        raise last


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_db_with_retry()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="MacroModel API", version="0.1.0", lifespan=lifespan)

    origins = ["*"] if settings.cors_origins.strip() == "*" else [
        o.strip() for o in settings.cors_origins.split(",") if o.strip()
    ]
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"],
    )

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "service": "macromodel-api", "env": settings.env}

    for r in (scenarios, network, network_edit, zones, counters, model, calibration, results,
              crud, procedures_router, matrices):
        app.include_router(r.router)
    return app


app = create_app()
