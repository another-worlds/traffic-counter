"""Background workspace backup jobs with cancel support."""
from __future__ import annotations

import logging
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..db import SessionLocal
from .workspace_backup import BackupCancelled, build_workspace_backup_zip

log = logging.getLogger("api.workspace_backup_jobs")

_LOCK = threading.Lock()
_JOBS: dict[str, "BackupJob"] = {}


@dataclass
class BackupJob:
    job_id: str
    scope: str
    folder: Optional[str] = None
    status: str = "pending"  # pending | running | done | error | cancelled
    error: Optional[str] = None
    cancel_requested: bool = False
    file_path: Optional[Path] = None
    filename: str = "workspace-backup.zip"
    video_count: int = 0
    storage_file_count: int = 0
    storage_packed: int = 0
    storage_total: int = 0
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None


def start_job(*, scope: str, folder: Optional[str], filename: str) -> BackupJob:
    job = BackupJob(
        job_id=uuid.uuid4().hex[:12],
        scope=scope,
        folder=folder,
        filename=filename,
    )
    with _LOCK:
        _JOBS[job.job_id] = job
    return job


def get(job_id: str) -> Optional[BackupJob]:
    with _LOCK:
        return _JOBS.get(job_id)


def _set(job_id: str, **fields) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        for key, value in fields.items():
            setattr(job, key, value)


def request_cancel(job_id: str) -> bool:
    with _LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return False
        if job.status in {"done", "error", "cancelled"}:
            return False
        job.cancel_requested = True
        return True


def gc_old_jobs(max_age_minutes: int = 120) -> int:
    cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
    removed = 0
    with _LOCK:
        for jid, job in list(_JOBS.items()):
            stamp = job.finished_at or job.started_at
            if stamp < cutoff:
                if job.file_path:
                    try:
                        job.file_path.unlink(missing_ok=True)
                    except Exception:
                        log.exception("janitor: failed to delete %s", job.file_path)
                _JOBS.pop(jid, None)
                removed += 1
    return removed


def run_backup_job(*, job_id: str) -> None:
    job = get(job_id)
    if not job:
        return
    scope = job.scope
    folder = job.folder
    _set(job_id, status="running")

    def cancel_cb() -> bool:
        current = get(job_id)
        return bool(current and current.cancel_requested)

    def progress_cb(packed: int, total: int) -> None:
        _set(job_id, storage_packed=packed, storage_total=total)

    try:
        with SessionLocal() as db:
            tmp_path, summary = build_workspace_backup_zip(
                db,
                scope=scope,
                folder=folder,
                cancel_cb=cancel_cb,
                progress_cb=progress_cb,
            )
        if cancel_cb():
            Path(tmp_path).unlink(missing_ok=True)
            _set(job_id, status="cancelled", finished_at=datetime.utcnow())
            return
        _set(
            job_id,
            status="done",
            file_path=Path(tmp_path),
            video_count=int(summary.get("video_count") or 0),
            storage_file_count=int(summary.get("storage_file_count") or 0),
            finished_at=datetime.utcnow(),
        )
    except BackupCancelled:
        _set(job_id, status="cancelled", finished_at=datetime.utcnow())
    except ValueError as exc:
        _set(job_id, status="error", error=str(exc), finished_at=datetime.utcnow())
    except Exception:
        log.exception("backup job %s failed", job_id)
        _set(
            job_id,
            status="error",
            error=traceback.format_exc(limit=3),
            finished_at=datetime.utcnow(),
        )