"""
Worker entrypoint.

Modes:
  - WORKER_MODE=poll        long-running loop, claims status='queued' videos
  - WORKER_MODE=single      processes one video (VIDEO_ID env var), then exits

The "single" mode is what Cloud Run Jobs invokes for each video. Per-execution
env overrides set VIDEO_ID and WORKER_MODE; the rest comes from the job's
configured environment.

Processing model (8-24h videos):
  Each video is divided into 1-hour segments.  The worker processes them one
  at a time, writing a per-segment parquet after each.  On any restart (planned
  docker-compose down/up, crash, OOM) the worker resumes from the first
  non-done segment — no special shutdown handler required.
"""
from __future__ import annotations
import os
import socket
import sys
import threading
import time
import logging
import traceback
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from pipeline import (
    video_meta,
    plan_video_segments,
    process_video_segment,
    finalize_video_post_processing,
    SEGMENT_DURATION_S,
)
from storage import get_storage, key_video

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


# ---------------------------------------------------------------------------
# Video-level DB helpers
# ---------------------------------------------------------------------------

def claim_one_queued():
    """Atomically claim one queued video (SELECT … FOR UPDATE SKIP LOCKED)."""
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
                        last_heartbeat_at=:ts,
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
        conn.execute(
            text("""UPDATE videos
                    SET status='analyzing',
                        error_message=NULL,
                        started_analyzing_at=:ts,
                        last_heartbeat_at=:ts,
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


def mark_analyzed(video_id: str, meta: dict, scene_frames: list, num_tracks: int):
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
                analyzed_at=:analyzed_at,
                last_heartbeat_at=:analyzed_at
            WHERE id=:id
        """), {
            "id": video_id,
            "fps": meta.get("fps"),
            "duration_s": (meta.get("num_frames") or 0) / (meta.get("fps") or 1),
            "width": meta.get("width"),
            "height": meta.get("height"),
            "num_frames": meta.get("num_frames"),
            "num_tracks": num_tracks,
            "scene_frames": _json.dumps(scene_frames),
            "analyzed_at": datetime.utcnow(),
        })


def mark_error(video_id: str, err: Exception, segment_context: str = ""):
    prefix = f"[{segment_context}] " if segment_context else ""
    msg = f"{prefix}{type(err).__name__}: {err}\n\n{traceback.format_exc()[-2000:]}"
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE videos
            SET status='error', error_message=:msg, progress_pct=NULL,
                last_heartbeat_at=:ts
            WHERE id=:id
        """), {"id": video_id, "msg": msg, "ts": datetime.utcnow()})


def update_progress(video_id: str, pct: float):
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE videos SET progress_pct=:pct, last_heartbeat_at=:ts "
                "WHERE id=:id"
            ),
            {"pct": round(pct, 4), "ts": datetime.utcnow(), "id": video_id},
        )


# ---------------------------------------------------------------------------
# Segment-level DB helpers
# ---------------------------------------------------------------------------

def plan_segments(video_id: str, fps: float, num_frames: int, segment_duration_s: float):
    """Create video_segment rows for this video (idempotent).

    Existing 'done' rows are left untouched.  Any row stuck in 'analyzing'
    (from a dead previous worker) is reset to 'pending' so this worker
    will re-process it.  Called at the start of every handle() so restarts
    automatically resume from the last completed segment.
    """
    segments = plan_video_segments(fps, num_frames, segment_duration_s)
    total = len(segments)
    with engine.begin() as conn:
        for seg in segments:
            conn.execute(text("""
                INSERT INTO video_segments
                    (id, video_id, segment_idx, status,
                     start_frame, end_frame, start_time_s, end_time_s)
                VALUES
                    (gen_random_uuid(), :video_id, :seg_idx, 'pending',
                     :start_frame, :end_frame, :start_time_s, :end_time_s)
                ON CONFLICT (video_id, segment_idx) DO NOTHING
            """), {
                "video_id": video_id,
                "seg_idx": seg["segment_idx"],
                "start_frame": seg["start_frame"],
                "end_frame": seg["end_frame"],
                "start_time_s": seg["start_time_s"],
                "end_time_s": seg["end_time_s"],
            })
        # Reset segments left 'analyzing' by a dead worker, and retry any
        # 'error' segments from a previous run so a re-analyze re-attempts
        # failed hours instead of silently skipping them.
        conn.execute(text("""
            UPDATE video_segments
            SET status='pending', started_at=NULL,
                last_heartbeat_at=NULL, error_message=NULL
            WHERE video_id=:video_id AND status IN ('analyzing', 'error')
        """), {"video_id": video_id})
        # Record total segment count on the video row.
        conn.execute(text("""
            UPDATE videos SET total_segments=:n, segment_duration_s=:dur
            WHERE id=:video_id
        """), {"n": total, "dur": segment_duration_s, "video_id": video_id})
    return total


def claim_next_pending_segment(video_id: str):
    """Atomically claim the next pending segment for this video."""
    with engine.begin() as conn:
        row = conn.execute(text("""
            SELECT id, segment_idx, start_frame, end_frame, start_time_s, end_time_s
            FROM video_segments
            WHERE video_id=:video_id AND status='pending'
            ORDER BY segment_idx ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        """), {"video_id": video_id}).first()
        if not row:
            return None
        conn.execute(text("""
            UPDATE video_segments
            SET status='analyzing', started_at=:ts, last_heartbeat_at=:ts
            WHERE id=:id
        """), {"id": row.id, "ts": datetime.utcnow()})
        return {
            "id": row.id,
            "segment_idx": row.segment_idx,
            "start_frame": row.start_frame,
            "end_frame": row.end_frame,
            "start_time_s": row.start_time_s,
            "end_time_s": row.end_time_s,
        }


def mark_segment_done(segment_id: str, num_tracks: int):
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE video_segments
            SET status='done', num_tracks=:n, completed_at=:ts, last_heartbeat_at=:ts
            WHERE id=:id
        """), {"id": segment_id, "n": num_tracks, "ts": datetime.utcnow()})


def mark_segment_error(segment_id: str, err: Exception):
    msg = f"{type(err).__name__}: {err}\n\n{traceback.format_exc()[-2000:]}"
    with engine.begin() as conn:
        conn.execute(text("""
            UPDATE video_segments
            SET status='error', error_message=:msg, last_heartbeat_at=:ts
            WHERE id=:id
        """), {"id": segment_id, "msg": msg, "ts": datetime.utcnow()})


def update_segment_heartbeat(segment_id: str):
    with engine.begin() as conn:
        conn.execute(text(
            "UPDATE video_segments SET last_heartbeat_at=:ts WHERE id=:id"
        ), {"id": segment_id, "ts": datetime.utcnow()})


def unfinished_segments(video_id: str) -> list[int]:
    """Return the segment indexes for this video that are not yet 'done'."""
    with engine.begin() as conn:
        rows = conn.execute(text("""
            SELECT segment_idx FROM video_segments
            WHERE video_id=:video_id AND status<>'done'
            ORDER BY segment_idx
        """), {"video_id": video_id}).all()
    return [r.segment_idx for r in rows]


# ---------------------------------------------------------------------------
# Heartbeat thread
# ---------------------------------------------------------------------------

HEARTBEAT_INTERVAL_S = float(os.environ.get("WORKER_HEARTBEAT_INTERVAL_S", "10"))


class Heartbeat:
    """Decoupled DB heartbeat / progress writer.

    Runs a daemon thread that persists the latest in-memory progress value
    and bumps the segment + video heartbeats on the wall clock.  Inference
    code only sets the in-memory value via set_progress().

    Usage:
        with Heartbeat(video_id, segment_id) as hb:
            num_tracks = process_video_segment(..., on_progress=hb.set_progress)
    """

    def __init__(
        self,
        video_id: str,
        segment_id: str | None = None,
        interval_s: float = HEARTBEAT_INTERVAL_S,
    ):
        self.video_id = video_id
        self.segment_id = segment_id
        self.interval = interval_s
        self._pct = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set_progress(self, pct: float) -> None:
        self._pct = float(pct)

    def _tick(self) -> None:
        try:
            update_progress(self.video_id, self._pct)
        except Exception:
            log.warning("heartbeat write failed for video %s", self.video_id, exc_info=True)
        if self.segment_id:
            try:
                update_segment_heartbeat(self.segment_id)
            except Exception:
                log.warning("heartbeat write failed for segment %s", self.segment_id, exc_info=True)

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            self._tick()

    def __enter__(self) -> "Heartbeat":
        self._thread = threading.Thread(
            target=self._run,
            name=f"heartbeat-{self.video_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval + 1)
        self._tick()


# ---------------------------------------------------------------------------
# Main processing logic
# ---------------------------------------------------------------------------

def _resolve_video_path(video: dict) -> str:
    """Return the local path to the video file, downloading if necessary."""
    if video.get("local_source_path"):
        return video["local_source_path"]
    storage = get_storage()
    import tempfile, shutil
    from pathlib import Path
    # For non-local-folder videos we need a stable path within a temp dir.
    # We use a module-level temp dir per video to avoid re-downloading on
    # each segment (the file may be large).
    tmp_dir = Path(tempfile.gettempdir()) / "worker_video_cache" / video["id"]
    tmp_dir.mkdir(parents=True, exist_ok=True)
    local_path = str(tmp_dir / Path(video["filename"]).name)
    if not Path(local_path).exists():
        src_key = key_video(video["project_id"], video["id"], video["filename"])
        storage.download_to(src_key, local_path)
    return local_path


_video_tmp_dirs: dict[str, str] = {}  # video_id → tmp dir path (for cleanup)


def handle(video: dict):
    log.info("processing video %s (%s)", video["id"], video["filename"])

    # Resolve local video path (download once if needed).
    local_video = _resolve_video_path(video)
    _video_tmp_dirs[video["id"]] = str(
        __import__("pathlib").Path(local_video).parent
    )

    meta = video_meta(local_video)
    fps = meta["fps"]
    num_frames = meta["num_frames"] or 1

    seg_dur = float(os.environ.get("SEGMENT_DURATION_S", str(SEGMENT_DURATION_S)))
    total_segs = plan_segments(video["id"], fps, num_frames, seg_dur)
    log.info("video %s: %d segments of %.0fs each", video["id"], total_segs, seg_dur)

    seg_done = 0
    seg_context = ""  # tracks the last segment for error reporting
    try:
        while True:
            seg = claim_next_pending_segment(video["id"])
            if seg is None:
                break

            seg_context = f"Segment {seg['segment_idx']+1}/{total_segs}"
            log.info(
                "video %s: segment %d/%d (frames %d–%d)",
                video["id"], seg["segment_idx"] + 1, total_segs,
                seg["start_frame"], seg["end_frame"],
            )

            with Heartbeat(video["id"], seg["id"]) as hb:
                try:
                    num_tracks = process_video_segment(
                        project_id=video["project_id"],
                        video_id=video["id"],
                        segment_idx=seg["segment_idx"],
                        video_path=local_video,
                        start_frame=seg["start_frame"],
                        end_frame=seg["end_frame"],
                        fps=fps,
                        on_progress=lambda pct, _hb=hb, _seg=seg, _done=seg_done, _total=total_segs: (
                            _hb.set_progress((_done + pct) / _total)
                        ),
                    )
                    mark_segment_done(seg["id"], num_tracks)
                    log.info(
                        "video %s: segment %d done (%d tracks)",
                        video["id"], seg["segment_idx"], num_tracks,
                    )
                except Exception as e:
                    log.exception("video %s segment %d failed", video["id"], seg["segment_idx"])
                    mark_segment_error(seg["id"], e)
                    raise  # propagate to outer handler → mark_error(video)

            seg_done += 1
            seg_context = ""

        # Never finalise with missing hours: if any segment failed to reach
        # 'done', fail the whole video loudly rather than aggregating a partial
        # result. A re-analyze resets these segments to 'pending' and retries.
        pending_or_failed = unfinished_segments(video["id"])
        if pending_or_failed:
            raise RuntimeError(
                f"{len(pending_or_failed)} of {total_segs} hour-segment(s) did not "
                f"complete (segment idx {pending_or_failed}); not finalizing. "
                f"Re-analyze to retry the failed hour(s)."
            )

        # All segments done — finalise (scenes, trajectory PNG, status update).
        seg_context = "Finalizing"
        log.info("video %s: finalising (scenes + trajectory render)", video["id"])
        with Heartbeat(video["id"]) as hb:
            hb.set_progress(0.9)
            scenes, num_tracks_total = finalize_video_post_processing(
                project_id=video["project_id"],
                video_id=video["id"],
                video_path=local_video,
                meta=meta,
                total_segments=total_segs,
                on_progress=hb.set_progress,
            )

        mark_analyzed(video["id"], meta, scenes, num_tracks_total)
        log.info("done %s: %d tracks across %d segments", video["id"], num_tracks_total, total_segs)

    except Exception as e:
        log.exception("video %s failed", video["id"])
        mark_error(video["id"], e, segment_context=seg_context)
    finally:
        # Clean up the temp download dir (not needed for local-folder videos).
        if not video.get("local_source_path"):
            import shutil
            tmp = _video_tmp_dirs.pop(video["id"], None)
            if tmp:
                try:
                    shutil.rmtree(tmp, ignore_errors=True)
                except Exception:
                    pass


def poll_loop():
    log.info("worker started in poll mode (host=%s pid=%d)", socket.gethostname(), os.getpid())
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
