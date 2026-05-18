"""Endpoints for the local-folder (Yandex Disk) auto-import feature.

Called by the watcher service and the Streamlit UI. Videos imported here are
never copied — the worker reads from local_source_path directly.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Project, Video
from ..schemas import VideoOut
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
