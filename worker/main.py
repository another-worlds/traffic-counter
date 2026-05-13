"""
Worker entrypoint.

Modes:
  WORKER_MODE=poll    long-running loop; claims status='queued' videos.
  WORKER_MODE=single  processes one video (VIDEO_ID env var), then exits.

Concurrency (poll mode only):
  WORKER_CONCURRENCY=N  spawn N independent worker processes inside this
  container, each with its own YOLO model and DB connection.  Defaults to 1.
  Uses multiprocessing 'spawn' start method so CUDA contexts are never forked
  (fork+CUDA = undefined behaviour / crashes).

  Alternatively, run multiple containers via docker-compose --scale worker=N;
  SELECT FOR UPDATE SKIP LOCKED handles contention safely in both cases.

Stuck-job recovery:
  On startup each worker re-queues videos that have been stuck in 'analyzing'
  state for longer than STUCK_JOB_TIMEOUT_MINUTES (default 30).
"""
from __future__ import annotations
import multiprocessing
import os
import signal
import sys
import time
import logging
import traceback
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("worker")

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://traffic:traffic@db:5432/traffic",
)
STUCK_TIMEOUT = int(os.environ.get("STUCK_JOB_TIMEOUT_MINUTES", "30"))

# Engine is module-level; each spawned child re-imports the module and gets
# its own fresh pool (safe).  Do NOT share an engine across fork().
engine = create_engine(DATABASE_URL, pool_pre_ping=True, future=True)


def claim_one_queued():
    """
    Atomically claim one queued video.  SELECT FOR UPDATE SKIP LOCKED lets
    multiple pollers run concurrently without racing on the same row.
    """
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT id, project_id, filename
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
        return {"id": row.id, "project_id": row.project_id, "filename": row.filename}


def fetch_video(video_id: str):
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT id, project_id, filename FROM videos WHERE id=:id"),
            {"id": video_id},
        ).first()
        if not row:
            return None
        conn.execute(
            text("""UPDATE videos
                    SET status='analyzing',
                        error_message=NULL,
                        started_analyzing_at=:ts,
                        progress_pct=0
                    WHERE id=:id"""),
            {"id": video_id, "ts": datetime.utcnow()},
        )
        return {"id": row.id, "project_id": row.project_id, "filename": row.filename}


def mark_analyzed(video_id: str, meta: dict):
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
            "analyzed_at": datetime.utcnow(),
        })


def mark_error(video_id: str, err: Exception):
    msg = f"{type(err).__name__}: {err}\n\n{traceback.format_exc()[-2000:]}"
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE videos SET status='error', error_message=:msg, progress_pct=NULL WHERE id=:id
        """), {"id": video_id, "msg": msg})


def update_progress(video_id: str, pct: float):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE videos SET progress_pct=:pct WHERE id=:id"),
            {"pct": round(pct, 4), "id": video_id},
        )


def recover_stuck_jobs():
    """Re-queue videos stuck in 'analyzing' beyond STUCK_TIMEOUT minutes."""
    cutoff = datetime.utcnow() - timedelta(minutes=STUCK_TIMEOUT)
    with engine.begin() as conn:
        result = conn.execute(text("""
            UPDATE videos
               SET status='queued', progress_pct=0
             WHERE status='analyzing'
               AND started_analyzing_at < :cutoff
            RETURNING id
        """), {"cutoff": cutoff})
        ids = [r[0] for r in result]
    if ids:
        log.warning("re-queued %d stuck job(s): %s", len(ids), ids)


def handle(video: dict):
    from pipeline import process_video  # deferred: each process loads its own model
    log.info("processing video %s (%s) pid=%d", video["id"], video["filename"], os.getpid())
    try:
        meta = process_video(
            project_id=video["project_id"],
            video_id=video["id"],
            filename=video["filename"],
            on_progress=lambda pct: update_progress(video["id"], pct),
        )
        mark_analyzed(video["id"], meta)
        log.info("done %s — %d tracks", video["id"], meta.get("num_tracks", 0))
    except Exception as e:
        log.exception("video %s failed", video["id"])
        mark_error(video["id"], e)


def poll_loop():
    log.info("poll loop started (pid=%d)", os.getpid())
    recover_stuck_jobs()
    while True:
        try:
            v = claim_one_queued()
        except Exception:
            log.exception("claim failed; retrying in 5 s")
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


# ── Multi-process bootstrap ──────────────────────────────────────────────────
def _child_worker():
    """Entry point for each spawned child process."""
    # Reset signal handlers so parent's SIGTERM handler doesn't get inherited.
    signal.signal(signal.SIGINT,  signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    poll_loop()


if __name__ == "__main__":
    mode        = os.environ.get("WORKER_MODE",        "poll")
    concurrency = int(os.environ.get("WORKER_CONCURRENCY", "1"))

    if mode == "single":
        single()
        sys.exit(0)

    if concurrency <= 1:
        poll_loop()
    else:
        # 'spawn' creates a clean interpreter in each child — required for CUDA.
        # 'fork' copies the parent's CUDA context which causes undefined behaviour.
        ctx = multiprocessing.get_context("spawn")
        log.info("spawning %d worker process(es)", concurrency)
        procs = [
            ctx.Process(target=_child_worker, name=f"worker-{i}", daemon=True)
            for i in range(concurrency)
        ]
        for p in procs:
            p.start()

        def _shutdown(sig, _frame):
            log.info("received signal %d — stopping %d worker(s)…", sig, len(procs))
            for p in procs:
                p.terminate()
            for p in procs:
                p.join(timeout=60)
            sys.exit(0)

        signal.signal(signal.SIGTERM, _shutdown)
        signal.signal(signal.SIGINT,  _shutdown)

        # Supervise: if a child dies unexpectedly, restart it.
        while True:
            for i, p in enumerate(procs):
                if not p.is_alive():
                    log.warning("worker-%d died (exit %s); restarting", i, p.exitcode)
                    new_p = ctx.Process(target=_child_worker, name=f"worker-{i}", daemon=True)
                    new_p.start()
                    procs[i] = new_p
            time.sleep(5)
