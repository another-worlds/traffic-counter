from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

from ..config import settings
from ..db import get_db
from ..models import Video, Project
from ..schemas import WorkerVideoStatus
from ..services.reaper import reap_stale_claims

router = APIRouter(tags=["worker"])


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


@router.post("/worker/reap-stale")
def reap_stale(db: Session = Depends(get_db)):
    """Synchronously flip every stuck 'analyzing' row to 'error'.

    Gives the operator a manual lever without waiting for the next reaper tick.
    """
    ids = reap_stale_claims(db, settings.stale_claim_threshold_seconds)
    return {"reaped": ids, "count": len(ids)}
