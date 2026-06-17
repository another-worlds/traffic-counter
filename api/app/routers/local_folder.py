"""Endpoints for the local-folder (Yandex Disk) auto-import feature.

Called by the watcher service and the Streamlit UI. Videos imported here are
never copied — the worker reads from local_source_path directly.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Project, TimestampScan, Video
from ..schemas import VideoOut
from ..services import workspace_backup, workspace_backup_jobs
from ..services.jobs import get_job_runner
from ..services.sources import find_workspace_for_path
from ..services.storage import get_storage, key_video

router = APIRouter(tags=["local-folder"])


class RegisterRequest(BaseModel):
    path: str
    auto_analyze: bool = False


class RegisterResponse(BaseModel):
    video_id: str
    is_new: bool
    status: str


class AnalyzePendingResponse(BaseModel):
    queued: int


class MetadataUpdateRequest(BaseModel):
    path: str
    fps: Optional[float] = None
    duration_s: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    num_frames: Optional[int] = None


class BackupJobRequest(BaseModel):
    scope: str = "all"
    folder: Optional[str] = None


class PruneRequest(BaseModel):
    video_id: str


class PruneResponse(BaseModel):
    deleted_id: str
    reindexed: bool
    new_video_id: Optional[str] = None
    local_source_path: Optional[str] = None


def _register_local_path(
    db: Session,
    request: Request,
    path: str,
    *,
    auto_analyze: bool,
) -> RegisterResponse:
    path = path.strip()

    existing = db.query(Video).filter(Video.local_source_path == path).first()
    if existing:
        return RegisterResponse(video_id=existing.id, is_new=False, status=existing.status)

    if not Path(path).is_file():
        raise HTTPException(400, f"path not accessible: {path}")

    cfg = getattr(request.app.state, "sources_config", None)
    ws = find_workspace_for_path(cfg, path)
    if ws is None:
        raise HTTPException(
            422,
            f"path {path} is outside any configured workspace; "
            "add it to config/sources.yaml or move the file under a listed folder",
        )

    project = (
        db.query(Project)
        .filter(Project.name == ws.name)
        .first()
    )
    if project is None:
        project = Project(
            name=ws.name,
            description=f"Yandex Disk: {ws.subpath}",
            local_source_root=str(ws.abs_path),
        )
        db.add(project)
        db.flush()

    filename = Path(path).name

    try:
        size_bytes = Path(path).stat().st_size
    except OSError:
        size_bytes = None

    status = "queued" if auto_analyze else "uploaded"

    v = Video(
        project_id=project.id,
        filename=filename,
        storage_path="",
        source="local-folder",
        local_source_path=path,
        size_bytes=size_bytes,
        status=status,
    )
    db.add(v)
    db.flush()
    v.storage_path = key_video(project.id, v.id, filename)

    db.commit()
    db.refresh(v)

    if auto_analyze:
        get_job_runner().enqueue(video_id=v.id, project_id=v.project_id)

    return RegisterResponse(video_id=v.id, is_new=True, status=v.status)


@router.post("/local-folder/register", response_model=RegisterResponse, status_code=200)
def register_local_video(
    body: RegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Register a video file from the local watched folder.

    Idempotent: returns the existing record if the path was already indexed.
    The file is NOT copied — the worker will read it directly from the given path.

    The owning workspace is resolved by longest-prefix match against the
    YAML-declared `local_source_root` per Project. Paths that fall outside
    every configured workspace folder are rejected with 422.
    """
    return _register_local_path(
        db, request, body.path, auto_analyze=body.auto_analyze,
    )


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


@router.get("/local-folder/paths", response_model=List[str])
def list_local_folder_paths(db: Session = Depends(get_db)):
    """Return all registered local_source_path values for watcher diff scans."""
    rows = (
        db.query(Video.local_source_path)
        .filter(Video.source == "local-folder", Video.local_source_path.isnot(None))
        .all()
    )
    return sorted({row[0] for row in rows if row[0]})


@router.post("/local-folder/update-metadata", response_model=VideoOut)
def update_local_video_metadata(body: MetadataUpdateRequest, db: Session = Depends(get_db)):
    """Upsert metadata for a watched-folder video by source path."""
    path = body.path.strip()
    v = db.query(Video).filter(Video.local_source_path == path).first()
    if not v:
        raise HTTPException(404, f"video not found for path: {path}")

    if body.fps is not None:
        v.fps = float(body.fps)
    if body.duration_s is not None:
        v.duration_s = float(body.duration_s)
    if body.width is not None:
        v.width = int(body.width)
    if body.height is not None:
        v.height = int(body.height)
    if body.num_frames is not None:
        v.num_frames = int(body.num_frames)

    db.commit()
    db.refresh(v)
    return v


def _safe_backup_slug(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(text).strip())
    return cleaned.strip("_") or "workspace"


def _backup_filename(scope: str, folder: str | None) -> str:
    stamp = folder if scope == "folder" else "all-workspaces"
    return f"workspace-backup-{_safe_backup_slug(stamp)}.zip"


@router.get("/local-folder/backup")
def download_workspace_backup(
    background_tasks: BackgroundTasks,
    scope: str = Query("all", pattern="^(all|folder)$"),
    folder: str | None = Query(None, description="Parent path key from the Watched Folder UI"),
    db: Session = Depends(get_db),
):
    """Download DB rows + derived artifacts for watched-folder workspace(s).

    Does not include Yandex source video files — only analysis artifacts and metadata.
    """
    try:
        tmp_path, summary = workspace_backup.build_workspace_backup_zip(
            db, scope=scope, folder=folder,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"backup failed: {exc}") from exc

    filename = _backup_filename(scope, folder)
    background_tasks.add_task(Path(tmp_path).unlink, missing_ok=True)
    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=filename,
        headers={"X-Backup-Video-Count": str(summary["video_count"])},
    )


@router.post("/local-folder/backup/jobs")
def start_workspace_backup_job(
    body: BackupJobRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Start a cancellable background workspace backup job."""
    if body.scope not in {"all", "folder"}:
        raise HTTPException(400, f"unsupported backup scope: {body.scope}")
    if body.scope == "folder" and not body.folder:
        raise HTTPException(400, "folder is required when scope=folder")

    videos = (
        db.query(Video)
        .filter(Video.source == "local-folder")
        .all()
    )
    if body.scope == "folder":
        if not body.folder:
            raise HTTPException(400, "folder is required when scope=folder")
        folder_videos = [
            v for v in videos
            if (v.local_source_path and str(Path(v.local_source_path).parent) == body.folder)
            or (not v.local_source_path and body.folder == "(unknown)")
        ]
        if not folder_videos:
            raise HTTPException(400, "no watched-folder videos match the requested backup scope")
    elif not videos:
        raise HTTPException(400, "no watched-folder videos match the requested backup scope")

    filename = _backup_filename(body.scope, body.folder)
    job = workspace_backup_jobs.start_job(
        scope=body.scope,
        folder=body.folder,
        filename=filename,
    )
    background_tasks.add_task(workspace_backup_jobs.run_backup_job, job_id=job.job_id)
    return {
        "job_id": job.job_id,
        "status": job.status,
        "filename": job.filename,
    }


@router.get("/local-folder/backup/jobs/{job_id}")
def workspace_backup_job_status(job_id: str):
    job = workspace_backup_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "job not found")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "error": job.error,
        "filename": job.filename,
        "video_count": job.video_count,
        "storage_file_count": job.storage_file_count,
        "storage_packed": job.storage_packed,
        "storage_total": job.storage_total,
        "scope": job.scope,
        "folder": job.folder,
    }


@router.delete("/local-folder/backup/jobs/{job_id}")
def cancel_workspace_backup_job(job_id: str):
    if not workspace_backup_jobs.request_cancel(job_id):
        job = workspace_backup_jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        raise HTTPException(409, f"job already {job.status}")
    return {"job_id": job_id, "status": "cancelling"}


@router.get("/local-folder/backup/jobs/{job_id}/file")
def workspace_backup_job_file(
    job_id: str,
    background_tasks: BackgroundTasks,
):
    job = workspace_backup_jobs.get(job_id)
    if not job or job.status != "done" or not job.file_path:
        raise HTTPException(404, "file not ready")
    path = job.file_path
    background_tasks.add_task(path.unlink, missing_ok=True)
    background_tasks.add_task(workspace_backup_jobs.gc_old_jobs)
    return FileResponse(
        path,
        media_type="application/zip",
        filename=job.filename,
        headers={"X-Backup-Video-Count": str(job.video_count)},
    )


@router.post("/local-folder/prune", response_model=PruneResponse)
def prune_local_folder_video(
    body: PruneRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Delete a watched-folder video record and re-register the source file."""
    v = db.get(Video, body.video_id)
    if not v:
        raise HTTPException(404, "video not found")
    if v.source != "local-folder":
        raise HTTPException(400, "only local-folder videos can be pruned from this endpoint")

    local_path = v.local_source_path
    deleted_id = v.id
    project_id = v.project_id
    vid = v.id

    scan = db.get(TimestampScan, vid)
    if scan and scan.status == "processing":
        raise HTTPException(
            409,
            "timestamp scan is still running — stop it on the Timestamp dashboard first",
        )

    db.delete(v)
    db.commit()

    try:
        get_storage().delete_prefix(f"projects/{project_id}/videos/{vid}")
    except Exception:
        pass

    new_video_id: str | None = None
    reindexed = False
    if local_path and Path(local_path).is_file():
        result = _register_local_path(
            db, request, local_path, auto_analyze=False,
        )
        if result.is_new:
            reindexed = True
            new_video_id = result.video_id

    return PruneResponse(
        deleted_id=deleted_id,
        reindexed=reindexed,
        new_video_id=new_video_id,
        local_source_path=local_path,
    )


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