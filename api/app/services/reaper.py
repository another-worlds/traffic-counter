"""Detect and resolve videos abandoned by a dead worker.

A worker process can die mid-job (SIGKILL, OOM, host reboot) without ever
running its ``mark_error`` handler. The video row then sits at
``status='analyzing'`` forever, and the watched-folder UI shows a frozen
progress bar. The reaper periodically flips such rows to ``status='error'``
with a descriptive message; the existing per-row "Analyze" button is the
re-queue surface.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models import Video


STALE_MSG = (
    "Worker stopped sending progress for over {minutes} min — likely a "
    "crash or container restart. Click Analyze to retry."
)


def reap_stale_claims(db: Session, threshold_seconds: int) -> List[str]:
    """Mark every stuck analyzing-row as error. Returns the affected ids."""
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
    ids: List[str] = []
    for v in stale:
        v.status = "error"
        v.error_message = STALE_MSG.format(minutes=minutes)
        v.progress_pct = None
        ids.append(str(v.id))
    if ids:
        db.commit()
    return ids
