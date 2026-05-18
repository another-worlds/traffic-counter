from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from ..db import get_db
from ..models import Video, Project, SystemState
from ..schemas import WorkerVideoStatus, PauseStateOut
from ..services.jobs import get_job_runner
from pydantic import BaseModel

router = APIRouter(tags=["worker"])


def _get_or_create_state(db: Session) -> SystemState:
    state = db.query(SystemState).filter(SystemState.id == 1).first()
    if not state:
        state = SystemState(id=1, processing_paused=False, updated_at=datetime.utcnow())
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


@router.get("/worker/status", response_model=List[WorkerVideoStatus])
def worker_status(db: Session = Depends(get_db)):
    """Return all videos currently queued or being analyzed, with project names."""
    rows = (
        db.query(Video, Project.name.label("project_name"))
        .join(Project, Video.project_id == Project.id)
        .filter(Video.status.in_(["queued", "analyzing"]))
        .order_by(Video.started_analyzing_at.asc().nullsfirst())
        .all()
    )
    return [
        WorkerVideoStatus(
            video_id=str(v.id),
            project_id=str(v.project_id),
            project_name=project_name,
            filename=v.filename,
            status=v.status,
            progress_pct=v.progress_pct or 0.0,
            started_analyzing_at=v.started_analyzing_at,
        )
        for v, project_name in rows
    ]


@router.get("/worker/pause-state", response_model=PauseStateOut)
def get_pause_state(db: Session = Depends(get_db)):
    state = _get_or_create_state(db)
    return PauseStateOut(paused=state.processing_paused)


@router.post("/worker/pause", response_model=PauseStateOut)
def pause_worker(db: Session = Depends(get_db)):
    """Pause the processing queue. The currently-analyzing video finishes first."""
    state = _get_or_create_state(db)
    state.processing_paused = True
    state.updated_at = datetime.utcnow()
    db.commit()
    return PauseStateOut(paused=True)


@router.post("/worker/resume", response_model=PauseStateOut)
def resume_worker(db: Session = Depends(get_db)):
    """Resume the processing queue."""
    state = _get_or_create_state(db)
    state.processing_paused = False
    state.updated_at = datetime.utcnow()
    db.commit()
    return PauseStateOut(paused=False)


class ErrorSummaryOut(BaseModel):
    total: int
    by_source: dict


@router.get("/worker/error-summary", response_model=ErrorSummaryOut)
def error_summary(db: Session = Depends(get_db)):
    """Per-source error counts (for surfacing 'retry all' UX everywhere)."""
    rows = (
        db.query(Video.source, Video.id)
        .filter(Video.status == "error")
        .all()
    )
    by_source: dict[str, int] = {}
    for src, _ in rows:
        by_source[src or "upload"] = by_source.get(src or "upload", 0) + 1
    return ErrorSummaryOut(total=len(rows), by_source=by_source)


class RetryErrorsOut(BaseModel):
    queued: int


@router.post("/worker/retry-errors", response_model=RetryErrorsOut)
def retry_errors(
    source: Optional[str] = Query(None, description="Limit to one source ('upload' or 'local-folder')"),
    project_id: Optional[str] = Query(None, description="Limit to one workspace"),
    db: Session = Depends(get_db),
):
    """Re-queue all error videos, optionally filtered by source and/or project."""
    q = db.query(Video).filter(Video.status == "error")
    if source:
        q = q.filter(Video.source == source)
    if project_id:
        q = q.filter(Video.project_id == project_id)

    videos = q.all()
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
    return RetryErrorsOut(queued=count)
