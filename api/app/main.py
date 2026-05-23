import asyncio
import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from .config import settings
from .db import SessionLocal, init_db
from .routers import projects, videos, lines, analysis, worker, upload_tus, local_folder
from .services.reaper import reap_stale_claims, reap_stale_segment_claims
from .services import sources as sources_svc
from .services import xlsx_jobs

REAPER_INTERVAL_S = 60
EXPORT_GC_MAX_AGE_MIN = 120
log = logging.getLogger("api.reaper")


def _reap_once() -> list[str]:
    with SessionLocal() as db:
        video_ids = reap_stale_claims(db, settings.stale_claim_threshold_seconds)
    with SessionLocal() as db:
        seg_ids = reap_stale_segment_claims(db, settings.stale_claim_threshold_seconds)
    return video_ids


def create_app() -> FastAPI:
    app = FastAPI(
        title="Traffic Counter API",
        version="0.1.0",
        description="Project-organized vehicle tracking and counting.",
    )

    # The iframe's effective origin depends on where the operator deploys
    # Streamlit (localhost in dev, an arbitrary public host in prod). A
    # static allowlist is brittle; we accept any origin and disable credentials
    # so the "*" + cookies combination remains spec-compliant.
    cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if cors_origins == ["*"] or not cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=".*",
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(projects.router)
    app.include_router(videos.router)
    app.include_router(lines.router)
    app.include_router(analysis.router)
    app.include_router(worker.router)
    app.include_router(upload_tus.router)
    app.include_router(local_folder.router)

    @app.on_event("startup")
    async def _startup():
        init_db()

        # Reconcile YAML-declared workspaces with the DB so the Streamlit
        # sidebar lists them from the first request. Degraded mode (file
        # missing or malformed) logs once and keeps the app booting.
        try:
            cfg = sources_svc.load_sources()
        except ValueError as exc:
            log.warning("sources: %s — auto-import disabled", exc)
            cfg = None
        app.state.sources_config = cfg
        if cfg is None:
            log.info("sources: config file missing or empty, auto-import disabled")
        else:
            try:
                with SessionLocal() as db:
                    report = sources_svc.sync_workspaces(db, cfg)
                log.info(report.summary())
                if report.errors:
                    for err in report.errors:
                        log.warning("sources: %s", err)
            except Exception:
                log.exception("sources: workspace sync failed")

        # One-shot pass first so a fresh deploy clears the existing backlog
        # of rows the previous (heartbeat-less) workers left behind.
        try:
            with SessionLocal() as db:
                reaped = reap_stale_claims(db, settings.stale_claim_threshold_seconds)
            with SessionLocal() as db:
                reap_stale_segment_claims(db, settings.stale_claim_threshold_seconds)
            if reaped:
                log.info("startup reaper: flipped %d stale video(s) to error", len(reaped))
        except Exception:
            log.exception("startup reaper failed")

        async def _reaper_loop():
            while True:
                await asyncio.sleep(REAPER_INTERVAL_S)
                try:
                    # Offload the blocking SQLAlchemy call so we don't stall the
                    # event loop while it talks to Postgres.
                    reaped = await asyncio.to_thread(_reap_once)
                    if reaped:
                        log.info("reaper: flipped %d stale video(s) to error", len(reaped))
                except Exception:
                    log.exception("reaper loop iteration failed")
                try:
                    dropped = xlsx_jobs.gc_old_jobs(max_age_minutes=EXPORT_GC_MAX_AGE_MIN)
                    if dropped:
                        log.info("janitor: dropped %d old export(s)", dropped)
                except Exception:
                    log.exception("export janitor iteration failed")

        asyncio.create_task(_reaper_loop())

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "env": settings.env, "storage": settings.storage_backend}

    # Local storage file serving — in prod this is replaced by signed GCS URLs.
    if settings.storage_backend == "local":
        @app.get("/files/{path:path}")
        def serve_file(path: str):
            full = Path(settings.local_storage_root) / path
            if not full.exists() or not full.is_file():
                raise HTTPException(404, "file not found")
            return FileResponse(full)

    return app


app = create_app()
