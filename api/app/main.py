from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path

from .config import settings
from .db import init_db
from .routers import projects, videos, lines, analysis, worker, upload_tus, local_folder


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
    def _startup():
        init_db()

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
