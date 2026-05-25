from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from datetime import datetime, timezone

from ..config import settings
from ..db import get_db
from ..models import Video, Project, VideoSegment
from ..schemas import WorkerVideoStatus
from ..services.reaper import reap_stale_claims

router = APIRouter(tags=["worker"])


def _fmt_hhmm(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}:{m:02d}"


def _build_worker_status(v: Video, project_name: str, segs: list) -> WorkerVideoStatus:
    current_seg_idx = None
    total_segs = v.total_segments
    completed_segs = 0
    eta_seconds = None
    speed_ratio = None
    status_text = None

    if v.status == "analyzing" and total_segs:
        done_segs = [s for s in segs if s.status == "done"]
        analyzing_seg = next((s for s in segs if s.status == "analyzing"), None)
        completed_segs = len(done_segs)

        # Compute avg wall-clock time per segment from completed ones.
        done_timed = [s for s in done_segs if s.completed_at and s.started_at]
        if done_timed:
            avg_wall_s = sum(
                (s.completed_at - s.started_at).total_seconds() for s in done_timed
            ) / len(done_timed)
            if avg_wall_s > 0:
                avg_vid_s = sum(
                    s.end_time_s - s.start_time_s for s in done_timed
                ) / len(done_timed)
                speed_ratio = avg_vid_s / avg_wall_s
                remaining = total_segs - completed_segs
                eta_seconds = remaining * avg_wall_s

        if analyzing_seg:
            current_seg_idx = analyzing_seg.segment_idx
            t0 = _fmt_hhmm(analyzing_seg.start_time_s)
            t1 = _fmt_hhmm(analyzing_seg.end_time_s)
            status_text = (
                f"Segment {analyzing_seg.segment_idx + 1} of {total_segs} "
                f"({t0}–{t1})"
            )
        elif completed_segs == total_segs:
            status_text = "Finalizing"
        else:
            status_text = "Starting…"

    return WorkerVideoStatus(
        video_id=str(v.id),
        project_id=str(v.project_id),
        project_name=project_name,
        filename=v.filename,
        status=v.status,
        progress_pct=v.progress_pct or 0.0,
        started_analyzing_at=v.started_analyzing_at,
        analyzed_at=v.analyzed_at,
        current_segment_idx=current_seg_idx,
        total_segments=total_segs,
        completed_segments=completed_segs if total_segs else None,
        eta_seconds=eta_seconds,
        speed_ratio=speed_ratio,
        worker_status_text=status_text,
    )


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

    # Bulk-load segments for all analyzing videos in one query.
    analyzing_ids = [str(v.id) for v, _ in rows if v.status == "analyzing" and v.total_segments]
    segs_by_video: dict = {}
    if analyzing_ids:
        all_segs = (
            db.query(VideoSegment)
            .filter(VideoSegment.video_id.in_(analyzing_ids))
            .all()
        )
        for s in all_segs:
            segs_by_video.setdefault(str(s.video_id), []).append(s)

    return [
        _build_worker_status(v, project_name, segs_by_video.get(str(v.id), []))
        for v, project_name in rows
    ]


@router.post("/worker/reap-stale")
def reap_stale(db: Session = Depends(get_db)):
    """Synchronously flip every stuck 'analyzing' row to 'error'.

    Gives the operator a manual lever without waiting for the next reaper tick.
    """
    ids = reap_stale_claims(db, settings.stale_claim_threshold_seconds)
    return {"reaped": ids, "count": len(ids)}
