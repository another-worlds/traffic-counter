"""
Worker entrypoint.

Modes:
  - WORKER_MODE=poll        long-running loop, claims status='queued' videos
  - WORKER_MODE=single      processes one video (VIDEO_ID env var), then exits

The "single" mode is what Cloud Run Jobs invokes for each video. Per-execution
env overrides set VIDEO_ID and WORKER_MODE; the rest comes from the job's
configured environment.
"""
from __future__ import annotations
import os
import signal
import sys
import time
import logging
import traceback
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from pipeline import process_video

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("worker")


DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://traffic:traffic@db:5432/traffic",
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)
Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

STALE_TIMEOUT_S      = int(os.environ.get("STALE_TIMEOUT_S", "300"))   # 5 min
MAX_RETRIES          = int(os.environ.get("MAX_RETRIES", "2"))
POLL_IDLE_S          = 3
POLL_PAUSED_S        = 10
PAUSE_CACHE_TTL_S    = 2   # re-read pause flag at most every 2s

_shutdown_requested  = False
_pause_cache: dict   = {"paused": False, "checked_at": 0.0}


# ── signal handling ───────────────────────────────────────────────────────────

def _request_shutdown(signum, frame):
    global _shutdown_requested
    log.info("shutdown signal received — will stop after current video")
    _shutdown_requested = True

signal.signal(signal.SIGTERM, _request_shutdown)
signal.signal(signal.SIGINT,  _request_shutdown)


# ── pause-state check (cached) ────────────────────────────────────────────────

def is_paused() -> bool:
    now = time.monotonic()
    if now - _pause_cache["checked_at"] < PAUSE_CACHE_TTL_S:
        return _pause_cache["paused"]
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT processing_paused FROM system_state WHERE id=1")
            ).first()
            paused = bool(row.processing_paused) if row else False
    except Exception:
        paused = False
    _pause_cache.update({"paused": paused, "checked_at": now})
    return paused


# ── stale-row sweeper ─────────────────────────────────────────────────────────

def sweep_stale_analyzing() -> None:
    """Reset 'analyzing' rows that haven't reported progress in STALE_TIMEOUT_S seconds."""
    try:
        with engine.begin() as conn:
            rows = conn.execute(text("""
                UPDATE videos
                   SET status          = CASE WHEN retries >= :max THEN 'error' ELSE 'queued' END,
                       error_message   = CASE WHEN retries >= :max
                                              THEN 'auto-recovery: exceeded max retries'
                                              ELSE NULL END,
                       retries         = retries + 1,
                       progress_pct    = NULL,
                       progress_updated_at = NULL,
                       started_analyzing_at = NULL
                 WHERE status = 'analyzing'
                   AND COALESCE(progress_updated_at, started_analyzing_at)
                     < NOW() - make_interval(secs => :sec)
                RETURNING id, status, retries
            """), {"max": MAX_RETRIES, "sec": STALE_TIMEOUT_S}).all()
            for row in rows:
                log.warning("auto-recovery: video %s → %s (retry %d)",
                            row.id, row.status, row.retries)
    except Exception:
        log.exception("sweep_stale_analyzing failed")


# ── job-claim mechanics ───────────────────────────────────────────────────────

def claim_one_queued():
    """Atomically claim one queued video (SELECT ... FOR UPDATE SKIP LOCKED)."""
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT id, project_id, filename, local_source_path
            FROM videos
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """)).first()
        if not row:
            return None
        now = datetime.utcnow()
        conn.execute(
            text("""UPDATE videos
                    SET status='analyzing',
                        started_analyzing_at=:ts,
                        progress_pct=0,
                        progress_updated_at=:ts
                    WHERE id=:id"""),
            {"id": row.id, "ts": now},
        )
        return {
            "id": row.id,
            "project_id": row.project_id,
            "filename": row.filename,
            "local_source_path": row.local_source_path,
        }


def fetch_video(video_id: str):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, project_id, filename, local_source_path FROM videos WHERE id=:id"),
            {"id": video_id},
        ).first()
        if not row:
            return None
        now = datetime.utcnow()
        conn.execute(
            text("""UPDATE videos
                    SET status='analyzing',
                        error_message=NULL,
                        started_analyzing_at=:ts,
                        progress_pct=0,
                        progress_updated_at=:ts
                    WHERE id=:id"""),
            {"id": video_id, "ts": now},
        )
        return {
            "id": row.id,
            "project_id": row.project_id,
            "filename": row.filename,
            "local_source_path": row.local_source_path,
        }


# ── status transitions ────────────────────────────────────────────────────────

def mark_analyzed(video_id: str, meta: dict):
    import json as _json
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE videos SET
                status='analyzed',
                error_message=NULL,
                progress_pct=NULL,
                progress_updated_at=NULL,
                fps=:fps,
                duration_s=:duration_s,
                width=:width,
                height=:height,
                num_frames=:num_frames,
                num_tracks=:num_tracks,
                scene_frames=:scene_frames,
                analyzed_at=:analyzed_at
            WHERE id=:id
        """), {
            "id": video_id,
            "fps": meta.get("fps"),
            "duration_s": (meta.get("num_frames") or 0) / (meta.get("fps") or 1),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "num_frames": meta.get("num_frames"),
            "num_tracks": meta.get("num_tracks"),
            "scene_frames": _json.dumps(meta.get("scene_frames") or []),
            "analyzed_at": datetime.utcnow(),
        })


def mark_error(video_id: str, err: Exception):
    msg = f"{type(err).__name__}: {err}\n\n{traceback.format_exc()[-2000:]}"
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE videos
            SET status='error',
                error_message=:msg,
                progress_pct=NULL,
                progress_updated_at=NULL
            WHERE id=:id
        """), {"id": video_id, "msg": msg})


def update_progress(video_id: str, pct: float):
    """Lightweight progress update called every ~50 frames during analysis."""
    with engine.begin() as conn:
        conn.execute(
            text("""UPDATE videos
                    SET progress_pct=:pct, progress_updated_at=NOW()
                    WHERE id=:id"""),
            {"pct": round(pct, 4), "id": video_id},
        )


# ── video processing ──────────────────────────────────────────────────────────

def handle(video: dict):
    log.info("processing video %s (%s)", video["id"], video["filename"])
    try:
        def _on_progress(pct: float):
            update_progress(video["id"], pct)

        meta = process_video(
            project_id=video["project_id"],
            video_id=video["id"],
            filename=video["filename"],
            on_progress=_on_progress,
            local_source_path=video.get("local_source_path"),
        )
        mark_analyzed(video["id"], meta)
        log.info("done %s: %s tracks", video["id"], meta.get("num_tracks"))
    except Exception as e:
        log.exception("video %s failed", video["id"])
        mark_error(video["id"], e)


# ── worker modes ──────────────────────────────────────────────────────────────

def poll_loop():
    log.info("worker started in poll mode")
    sweep_stale_analyzing()   # recover any rows stuck from a previous crash
    while not _shutdown_requested:
        sweep_stale_analyzing()
        if is_paused():
            log.debug("processing paused — sleeping %ds", POLL_PAUSED_S)
            time.sleep(POLL_PAUSED_S)
            continue
        try:
            v = claim_one_queued()
        except Exception:
            log.exception("claim failed; retrying")
            time.sleep(5)
            continue
        if not v:
            time.sleep(POLL_IDLE_S)
            continue
        handle(v)
    log.info("worker stopped cleanly")


def single():
    video_id = os.environ.get("VIDEO_ID")
    if not video_id:
        log.error("WORKER_MODE=single requires VIDEO_ID")
        sys.exit(2)
    v = fetch_video(video_id)
    if not v:
        log.error("video %s not found", video_id)
        sys.exit(3)
    handle(v)


if __name__ == "__main__":
    mode = os.environ.get("WORKER_MODE", "poll")
    if mode == "single":
        single()
    else:
        poll_loop()
