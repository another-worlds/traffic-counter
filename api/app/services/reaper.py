"""Detect and resolve videos abandoned by a dead worker.

A worker process can die mid-job (SIGKILL, OOM, host reboot) without
running its mark_error handler — the row then sits at status='analyzing'
forever. The reaper periodically requeues such rows so another worker
picks them up. After a few failed attempts it falls back to surfacing
the row as 'error', so a video that genuinely cannot be processed
doesn't loop forever.

Segment-level reaping: individual video_segments stuck at 'analyzing'
without a recent heartbeat are reset to 'pending'.  The next worker
that claims the parent video will resume from those segments.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Video, VideoSegment


STALE_MSG = (
    "Worker stopped sending progress for over {minutes} min on attempt {attempts} "
    "— likely a crash or container restart. "
    "Completed hour-segments are preserved as checkpoints; "
    "processing will resume from the last completed segment. "
    "Click Analyze to retry."
)


def reap_stale_claims(db: Session, threshold_seconds: int) -> List[str]:
    """Requeue (or finally fail) every stuck analyzing-row. Returns the affected ids."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold_seconds)
    cutoff_naive = cutoff.replace(tzinfo=None)

    stale = (
        db.query(Video)
        .filter(Video.status == "analyzing")
        .filter(
            or_(
                Video.last_heartbeat_at < cutoff_naive,
                and_(
                    Video.last_heartbeat_at.is_(None),
                    Video.started_analyzing_at < cutoff_naive,
                ),
            )
        )
        .all()
    )

    minutes = max(1, threshold_seconds // 60)
    max_attempts = max(1, int(settings.max_analyze_attempts))
    ids: List[str] = []
    for v in stale:
        attempts = int(v.analyze_attempts or 0) + 1
        v.analyze_attempts = attempts
        v.started_analyzing_at = None
        v.last_heartbeat_at = None
        v.progress_pct = None
        if attempts < max_attempts:
            v.status = "queued"
            v.error_message = None
        else:
            v.status = "error"
            v.error_message = STALE_MSG.format(minutes=minutes, attempts=attempts)
        ids.append(str(v.id))
    if ids:
        db.commit()
    return ids


def reap_stale_segment_claims(db: Session, threshold_seconds: int) -> List[str]:
    """Reset individual video_segments stuck at 'analyzing' without a recent
    heartbeat back to 'pending'.

    The segment's parent video heartbeat is updated by the worker's Heartbeat
    thread, so a stale segment heartbeat implies the worker is genuinely dead
    or has moved on.  Resetting to 'pending' lets the next video claim pick
    it up via plan_segments() + claim_next_pending_segment().
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold_seconds)
    cutoff_naive = cutoff.replace(tzinfo=None)

    stale_segs = (
        db.query(VideoSegment)
        .filter(VideoSegment.status == "analyzing")
        .filter(
            or_(
                VideoSegment.last_heartbeat_at < cutoff_naive,
                and_(
                    VideoSegment.last_heartbeat_at.is_(None),
                    VideoSegment.started_at < cutoff_naive,
                ),
            )
        )
        .all()
    )

    seg_ids: List[str] = []
    for seg in stale_segs:
        seg.status = "pending"
        seg.started_at = None
        seg.last_heartbeat_at = None
        seg_ids.append(str(seg.id))

    if seg_ids:
        db.commit()
    return seg_ids
