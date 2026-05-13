from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from typing import List

from ..db import get_db
from ..models import Project, Video
from ..schemas import VideoOut, AnalyzeResponse
from ..services.storage import (
    get_storage, key_video, key_frame, key_trajectories,
)
from ..services.jobs import get_job_runner

router = APIRouter(tags=["videos"])


@router.get("/projects/{project_id}/videos", response_model=List[VideoOut])
def list_videos(project_id: str, db: Session = Depends(get_db)):
    if not db.get(Project, project_id):
        raise HTTPException(404, "project not found")
    return (
        db.query(Video)
        .filter(Video.project_id == project_id)
        .order_by(Video.created_at.desc())
        .all()
    )


@router.post("/projects/{project_id}/videos", response_model=VideoOut, status_code=201)
def upload_video(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """
    Direct upload through the API.

    For production, you would prefer a separate "presign upload" endpoint that
    returns a GCS signed URL and have the client PUT directly to it — skipping
    this byte stream through the API. Kept simple here.
    """
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")

    v = Video(project_id=project_id, filename=file.filename or "video.mp4",
              storage_path="", status="uploaded")
    db.add(v)
    db.flush()  # get v.id

    v.storage_path = key_video(project_id, v.id, v.filename)
    get_storage().upload_stream(v.storage_path, file.file)

    db.commit()
    db.refresh(v)
    return v


@router.get("/videos/{video_id}", response_model=VideoOut)
def get_video(video_id: str, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    return v


@router.post("/videos/{video_id}/analyze", response_model=AnalyzeResponse)
def analyze_video(video_id: str, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    if v.status in ("queued", "analyzing"):
        return AnalyzeResponse(video_id=video_id, status=v.status)

    v.status = "queued"
    v.error_message = None
    db.commit()

    get_job_runner().enqueue(video_id=video_id, project_id=v.project_id)
    return AnalyzeResponse(video_id=video_id, status="queued")


@router.get("/videos/{video_id}/frame-url")
def get_frame_url(video_id: str, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    storage = get_storage()
    k = key_frame(v.project_id, v.id)
    if not storage.exists(k):
        raise HTTPException(404, "frame not ready yet — analyze the video first")
    return {"url": storage.signed_url(k, expires_minutes=60)}


@router.get("/videos/{video_id}/trajectories-url")
def get_trajectories_url(video_id: str, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    storage = get_storage()
    k = key_trajectories(v.project_id, v.id)
    if not storage.exists(k):
        raise HTTPException(404, "trajectories not ready yet")
    return {"url": storage.signed_url(k, expires_minutes=60)}


@router.delete("/videos/{video_id}", status_code=204)
def delete_video(video_id: str, db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v:
        raise HTTPException(404, "video not found")
    project_id = v.project_id
    vid = v.id
    db.delete(v)
    db.commit()
    try:
        get_storage().delete_prefix(f"projects/{project_id}/videos/{vid}")
    except Exception:
        pass
