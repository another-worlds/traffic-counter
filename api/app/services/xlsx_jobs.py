"""Background xlsx export jobs.

The synchronous POST /export used to block Streamlit's script thread long
enough for Tornado to drop the websocket; the page would appear to crash.
We now hand the work to FastAPI's BackgroundTasks, store the artifact on
disk under ``<local_storage_root>/exports/<job_id>.xlsx``, and let the
Streamlit page poll for completion. Janitor pass evicts files after a
couple of hours so /data doesn't grow unboundedly.

In-process registry; the public-test API runs a single replica so a
shared store isn't needed. A multi-replica deploy would have to encode
the replica into the job id or move state into Postgres.
"""
from __future__ import annotations

import logging
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from ..config import settings
from ..db import SessionLocal
from ..models import CountingLine, Project, Video, VideoSegment
from .tracks import load_tracks_for_video
from .xlsx_export import build_xlsx_for_video

log = logging.getLogger("api.xlsx_jobs")

_LOCK = threading.Lock()
_JOBS: dict[str, "XlsxJob"] = {}


def _exports_dir() -> Path:
    p = Path(settings.local_storage_root) / "exports"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class XlsxJob:
    job_id: str
    video_id: str
    status: str = "pending"  # pending | running | done | error
    error: Optional[str] = None
    file_path: Optional[Path] = None
    filename: str = "counts.xlsx"
    started_at: datetime = field(default_factory=datetime.utcnow)
    finished_at: Optional[datetime] = None


def start_job(video_id: str, filename: str) -> XlsxJob:
    job = XlsxJob(job_id=uuid.uuid4().hex[:12], video_id=video_id, filename=filename)
    with _LOCK:
        _JOBS[job.job_id] = job
    return job


def get(job_id: str) -> Optional[XlsxJob]:
    with _LOCK:
        return _JOBS.get(job_id)


def _set(job_id: str, **fields) -> None:
    with _LOCK:
        j = _JOBS.get(job_id)
        if not j:
            return
        for k, v in fields.items():
            setattr(j, k, v)


def gc_old_jobs(max_age_minutes: int = 120) -> int:
    """Drop registry entries (and their on-disk files) older than the cutoff."""
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


def run_export_job(
    *,
    job_id: str,
    project_id: str,
    project_name: str,
    video_id: str,
    video_filename: str,
    line_ids: List[str],
) -> None:
    """Background worker — opens its own DB session, builds the workbook,
    writes it to disk, updates the registry. Catches *everything* so a
    failure doesn't crash the FastAPI event loop."""
    _set(job_id, status="running")
    try:
        with SessionLocal() as db:
            lines = (
                db.query(CountingLine)
                .filter(
                    CountingLine.video_id == video_id,
                    CountingLine.id.in_(line_ids),
                )
                .all()
            )
            if not lines:
                raise RuntimeError("no lines resolved for this video")

            lines_dict = [
                {
                    "id": ln.id,
                    "name": ln.name,
                    "a": ln.points["a"],
                    "b": ln.points["b"],
                }
                for ln in lines
            ]
            tracks_df = load_tracks_for_video(project_id, video_id)

            # Collect per-hour segment metadata (if any) for the xlsx builder.
            segments = (
                db.query(VideoSegment)
                .filter(
                    VideoSegment.video_id == video_id,
                    VideoSegment.status == "done",
                )
                .order_by(VideoSegment.segment_idx)
                .all()
            )
            segments_dict = [
                {
                    "segment_idx": s.segment_idx,
                    "start_frame": s.start_frame,
                    "end_frame": s.end_frame,
                    "start_time_s": s.start_time_s,
                    "end_time_s": s.end_time_s,
                }
                for s in segments
            ] if segments else None

            data = build_xlsx_for_video(
                project_name=project_name,
                video_filename=video_filename,
                tracks_df=tracks_df,
                lines=lines_dict,
                segments=segments_dict,
            )

            # Persist alongside other artifacts.
            out_path = _exports_dir() / f"{job_id}.xlsx"
            out_path.write_bytes(data)

            # Cosmetic timestamp on the project.
            project = db.get(Project, project_id)
            if project:
                project.last_exported_at = datetime.utcnow()
                db.commit()

        _set(
            job_id,
            status="done",
            file_path=out_path,
            finished_at=datetime.utcnow(),
        )
        log.info("xlsx job %s done (%s)", job_id, out_path.name)
    except Exception as exc:  # noqa: BLE001 — must catch everything
        log.exception("xlsx job %s failed", job_id)
        _set(
            job_id,
            status="error",
            error=f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()[-1500:]}",
            finished_at=datetime.utcnow(),
        )
