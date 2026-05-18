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


def claim_one_queued():
    """
    Atomically claim one queued video. Uses SELECT ... FOR UPDATE SKIP LOCKED
    to keep concurrent pollers from racing on the same row.
    """
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
        conn.execute(
            text("""UPDATE videos
                    SET status='analyzing',
                        started_analyzing_at=:ts,
                        progress_pct=0
                    WHERE id=:id"""),
            {"id": row.id, "ts": datetime.utcnow()},
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
        # Mark as analyzing
        conn.execute(
            text("""UPDATE videos
                    SET status='analyzing',
                        error_message=NULL,
                        started_analyzing_at=:ts,
                        progress_pct=0
                    WHERE id=:id"""),
            {"id": video_id, "ts": datetime.utcnow()},
        )
        return {
            "id": row.id,
            "project_id": row.project_id,
            "filename": row.filename,
            "local_source_path": row.local_source_path,
        }


def mark_analyzed(video_id: str, meta: dict):
    import json as _json
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE videos SET
                status='analyzed',
                error_message=NULL,
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
            UPDATE videos SET status='error', error_message=:msg, progress_pct=NULL WHERE id=:id
        """), {"id": video_id, "msg": msg})


def update_progress(video_id: str, pct: float):
    """Lightweight progress update (called every ~50 frames during analysis)."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE videos SET progress_pct=:pct WHERE id=:id"),
            {"pct": round(pct, 4), "id": video_id},
        )


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


def poll_loop():
    log.info("worker started in poll mode")
    while True:
        try:
            v = claim_one_queued()
        except Exception:
            log.exception("claim failed; retrying")
            time.sleep(5)
            continue
        if not v:
            time.sleep(3)
            continue
        handle(v)


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
