"""Endpoints for the local-folder (Yandex Disk) auto-import feature.

Called by the watcher service and the Streamlit UI. Videos imported here are
never copied — the worker reads from local_source_path directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Project, Video, SystemState
from ..schemas import (
    VideoOut,
    LocalFolderDashboard,
    DashboardCounts,
    DashboardAnalyzing,
    DashboardError,
    RetryErrorsResponse,
)
from ..services.jobs import get_job_runner
from ..services.storage import key_video

router = APIRouter(tags=["local-folder"])

INBOX_PROJECT_NAME = "Yandex Disk Inbox"


class RegisterRequest(BaseModel):
    path: str
    auto_analyze: bool = False


class RegisterResponse(BaseModel):
    video_id: str
    is_new: bool
    status: str


class AnalyzePendingResponse(BaseModel):
    queued: int


def _get_or_create_inbox(db: Session) -> Project:
    project = db.query(Project).filter(Project.name == INBOX_PROJECT_NAME).first()
    if not project:
        project = Project(name=INBOX_PROJECT_NAME, description="Auto-imported from watched folder")
        db.add(project)
        db.flush()
    return project


@router.post("/local-folder/register", response_model=RegisterResponse, status_code=200)
def register_local_video(body: RegisterRequest, db: Session = Depends(get_db)):
    """Register a video file from the local watched folder.

    Idempotent: returns the existing record if the path was already indexed.
    The file is NOT copied — the worker will read it directly from the given path.
    """
    path = body.path.strip()

    # Dedup: same physical file already registered.
    existing = db.query(Video).filter(Video.local_source_path == path).first()
    if existing:
        return RegisterResponse(video_id=existing.id, is_new=False, status=existing.status)

    inbox = _get_or_create_inbox(db)
    filename = Path(path).name

    try:
        size_bytes = Path(path).stat().st_size
    except OSError:
        size_bytes = None

    status = "queued" if body.auto_analyze else "uploaded"

    v = Video(
        project_id=inbox.id,
        filename=filename,
        storage_path="",          # filled after flush
        source="local-folder",
        local_source_path=path,
        size_bytes=size_bytes,
        status=status,
    )
    db.add(v)
    db.flush()
    v.storage_path = key_video(inbox.id, v.id, filename)

    db.commit()
    db.refresh(v)

    if body.auto_analyze:
        get_job_runner().enqueue(video_id=v.id, project_id=v.project_id)

    return RegisterResponse(video_id=v.id, is_new=True, status=v.status)


@router.get("/local-folder/videos", response_model=List[VideoOut])
def list_local_folder_videos(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List all videos imported from the local watched folder."""
    q = db.query(Video).filter(Video.source == "local-folder")
    if status:
        allowed = set(status.split(","))
        q = q.filter(Video.status.in_(allowed))
    return q.order_by(Video.created_at.desc()).all()


@router.post("/local-folder/analyze-pending", response_model=AnalyzePendingResponse)
def analyze_pending(db: Session = Depends(get_db)):
    """Queue all local-folder videos that have not been analyzed yet."""
    videos = (
        db.query(Video)
        .filter(Video.source == "local-folder", Video.status == "uploaded")
        .all()
    )
    runner = get_job_runner()
    count = 0
    for v in videos:
        v.status = "queued"
        v.error_message = None
        runner.enqueue(video_id=v.id, project_id=v.project_id)
        count += 1
    db.commit()
    return AnalyzePendingResponse(queued=count)


@router.get("/local-folder/dashboard", response_model=LocalFolderDashboard)
def local_folder_dashboard(db: Session = Depends(get_db)):
    """Single-call dashboard for the watched-folder page."""
    # ── pause state ────────────────────────────────────────────────────────────
    state = db.query(SystemState).filter(SystemState.id == 1).first()
    paused = state.processing_paused if state else False

    # ── aggregate counts (single query) ───────────────────────────────────────
    counts_row = db.query(
        func.count().label("total"),
        func.count(case((Video.status == "uploaded",  Video.id), else_=None)).label("uploaded"),
        func.count(case((Video.status == "queued",    Video.id), else_=None)).label("queued"),
        func.count(case((Video.status == "analyzing", Video.id), else_=None)).label("analyzing"),
        func.count(case((Video.status == "analyzed",  Video.id), else_=None)).label("analyzed"),
        func.count(case((Video.status == "error",     Video.id), else_=None)).label("error"),
    ).filter(Video.source == "local-folder").one()

    counts = DashboardCounts(
        total=counts_row.total,
        uploaded=counts_row.uploaded,
        queued=counts_row.queued,
        analyzing=counts_row.analyzing,
        analyzed=counts_row.analyzed,
        error=counts_row.error,
    )

    # ── currently analyzing ────────────────────────────────────────────────────
    analyzing_rows = (
        db.query(Video)
        .filter(Video.source == "local-folder", Video.status == "analyzing")
        .order_by(Video.started_analyzing_at.asc().nullsfirst())
        .all()
    )
    now = datetime.utcnow()
    currently_analyzing = []
    for v in analyzing_rows:
        pct = v.progress_pct or 0.0
        eta_s = None
        if v.started_analyzing_at and pct > 0.05:
            elapsed = (now - v.started_analyzing_at).total_seconds()
            eta_s = round(elapsed / pct * (1.0 - pct))
        currently_analyzing.append(DashboardAnalyzing(
            id=str(v.id),
            filename=v.filename,
            progress_pct=pct,
            started_at=v.started_analyzing_at,
            eta_s=eta_s,
        ))

    # ── throughput & avg analysis time (last 20 analyzed) ─────────────────────
    one_hour_ago = now - timedelta(hours=1)
    throughput_per_hour = db.query(func.count(Video.id)).filter(
        Video.source == "local-folder",
        Video.status == "analyzed",
        Video.analyzed_at >= one_hour_ago,
    ).scalar() or 0

    recent_analyzed = (
        db.query(Video.started_analyzing_at, Video.analyzed_at)
        .filter(
            Video.source == "local-folder",
            Video.status == "analyzed",
            Video.started_analyzing_at.isnot(None),
            Video.analyzed_at.isnot(None),
        )
        .order_by(Video.analyzed_at.desc())
        .limit(20)
        .all()
    )
    avg_analysis_seconds: Optional[float] = None
    if recent_analyzed:
        durations = [
            (r.analyzed_at - r.started_analyzing_at).total_seconds()
            for r in recent_analyzed
            if r.analyzed_at > r.started_analyzing_at
        ]
        if durations:
            avg_analysis_seconds = round(sum(durations) / len(durations))

    queue_eta_seconds: Optional[float] = None
    if avg_analysis_seconds is not None:
        remaining = counts.queued + max(counts.analyzing - 1, 0)
        queue_eta_seconds = avg_analysis_seconds * remaining

    # ── recent errors ──────────────────────────────────────────────────────────
    error_rows = (
        db.query(Video)
        .filter(Video.source == "local-folder", Video.status == "error")
        .order_by(Video.analyzed_at.desc().nullslast(), Video.created_at.desc())
        .limit(10)
        .all()
    )
    recent_errors = [
        DashboardError(
            id=str(v.id),
            filename=v.filename,
            error_message=v.error_message[:120] if v.error_message else None,
            retries=v.retries or 0,
        )
        for v in error_rows
    ]

    return LocalFolderDashboard(
        paused=paused,
        counts=counts,
        currently_analyzing=currently_analyzing,
        throughput_per_hour=float(throughput_per_hour),
        avg_analysis_seconds=avg_analysis_seconds,
        queue_eta_seconds=queue_eta_seconds,
        recent_errors=recent_errors,
    )


@router.post("/local-folder/retry-errors", response_model=RetryErrorsResponse)
def retry_errors(db: Session = Depends(get_db)):
    """Re-queue all local-folder videos that are in error state."""
    videos = (
        db.query(Video)
        .filter(Video.source == "local-folder", Video.status == "error")
        .all()
    )
    runner = get_job_runner()
    count = 0
    for v in videos:
        v.status = "queued"
        v.error_message = None
        v.retries = 0
        v.progress_pct = None
        runner.enqueue(video_id=v.id, project_id=v.project_id)
        count += 1
    db.commit()
    return RetryErrorsResponse(queued=count)
