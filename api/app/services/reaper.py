"""Detect and resolve videos abandoned by a dead worker.

A worker process can die mid-job (SIGKILL, OOM, host reboot) without
running its mark_error handler — the row then sits at status='analyzing'
forever. The reaper periodically requeues such rows so another worker
picks them up. After a few failed attempts it falls back to surfacing
the row as 'error', so a video that genuinely cannot be processed
doesn't loop forever.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Video


STALE_MSG = (
    "Worker stopped sending progress for over {minutes} min on {attempts} "
    "attempt(s) — likely a crash or container restart. Click Analyze to retry."
)


def reap_stale_claims(db: Session, threshold_seconds: int) -> List[str]:
    """Requeue (or finally fail) every stuck analyzing-row. Returns the affected ids."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=threshold_seconds)
    # Existing rows may have naive timestamps (UTC); strip tzinfo for the
    # comparison so SQLAlchemy doesn't reject the bind on Postgres TIMESTAMP
    # (without time zone) columns.
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
        # Always clear the claim fields so a requeued row looks fresh to the
        # next worker poll, and a permanently-errored row doesn't keep a
        # confusing "started 2h ago" timestamp.
        v.started_analyzing_at = None
        v.last_heartbeat_at = None
        v.progress_pct = None
        if attempts < max_attempts:
            # Hand it back to the queue — a different worker (or the same
            # one after a container restart) picks it up on the next poll.
            v.status = "queued"
            v.error_message = None
        else:
            v.status = "error"
            v.error_message = STALE_MSG.format(minutes=minutes, attempts=attempts)
        ids.append(str(v.id))
    if ids:
        db.commit()
    return ids
