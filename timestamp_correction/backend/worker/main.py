"""
Timestamp correction worker.

Modes:
  WORKER_MODE=poll   — auto-queue analyzed videos missing timestamp scans
  WORKER_MODE=single — process VIDEO_ID then exit
"""
from __future__ import annotations

import logging
import os
import time
import traceback
from datetime import datetime, timezone

from app import counter_client
from app.db import load_scan
from app.services.gap_map import write_video_index
from app.services.scan_control import is_auto_scan_enabled
from app.storage import get_storage, key_timestamp_status

from .pipeline import process_video
from .video_decode import configure_quiet_decode

configure_quiet_decode()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("timestamp_correction.worker")

POLL_INTERVAL_S = int(os.environ.get("POLL_INTERVAL_S", "30"))
STALE_PROCESSING_S = int(os.environ.get("STALE_PROCESSING_S", "7200"))
WORKER_MODE = os.environ.get("WORKER_MODE", "poll")
VIDEO_ID = os.environ.get("VIDEO_ID", "")
AUTO_SCAN_ENABLED = os.environ.get("AUTO_SCAN_ENABLED", "true").lower() in ("1", "true", "yes")


def _read_status(project_id: str, video_id: str) -> dict | None:
    db_row = load_scan(video_id)
    if db_row:
        return db_row
    storage = get_storage()
    status_key = key_timestamp_status(project_id, video_id)
    if not storage.exists(status_key):
        return None
    return storage.read_json(status_key)


def _is_stale_processing(status: dict) -> bool:
    if status.get("status") != "processing":
        return False
    started_raw = status.get("started_at")
    if not started_raw:
        return True
    try:
        started = datetime.fromisoformat(str(started_raw).replace("Z", "+00:00"))
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        age_s = (datetime.now(timezone.utc) - started).total_seconds()
        return age_s > STALE_PROCESSING_S
    except ValueError:
        return True


def _needs_scan(project_id: str, video_id: str) -> bool:
    if not is_auto_scan_enabled(project_id, video_id):
        return False
    status = _read_status(project_id, video_id)
    if status is None:
        return True
    if status.get("status") == "done":
        return False
    if status.get("status") == "cancelled":
        return False
    if status.get("status") == "processing":
        return _is_stale_processing(status)
    return status.get("status") in ("pending", "error", None)


def _find_pending_videos() -> list[dict]:
    pending: list[dict] = []
    for project in counter_client.list_projects():
        for video in counter_client.list_videos(project["id"]):
            if video.get("status") != "analyzed":
                continue
            if _needs_scan(project["id"], video["id"]):
                pending.append({
                    "id": video["id"],
                    "project_id": project["id"],
                    "filename": video["filename"],
                    "local_source_path": video.get("local_source_path"),
                })
    return pending


def _process_one(video: dict) -> None:
    log.info("scanning timestamps for %s (%s)", video["id"], video["filename"])
    write_video_index(
        video["project_id"],
        video["id"],
        video["filename"],
        video.get("local_source_path"),
    )
    process_video(
        project_id=video["project_id"],
        video_id=video["id"],
        filename=video["filename"],
        local_source_path=video.get("local_source_path"),
    )


def run_poll() -> None:
    if not AUTO_SCAN_ENABLED:
        log.info("AUTO_SCAN_ENABLED=false — worker idle")
        while True:
            time.sleep(POLL_INTERVAL_S)
        return

    log.info(
        "timestamp correction worker started (poll mode, interval=%ds)",
        POLL_INTERVAL_S,
    )
    while True:
        try:
            pending = _find_pending_videos()
            if pending:
                log.info("timestamp scan queue: %d video(s) pending", len(pending))
                _process_one(pending[0])
            else:
                log.debug("no videos pending timestamp scan")
        except Exception:
            log.error("poll iteration failed:\n%s", traceback.format_exc())
        time.sleep(POLL_INTERVAL_S)


def run_single() -> None:
    if not VIDEO_ID:
        raise SystemExit("VIDEO_ID required for WORKER_MODE=single")
    video = counter_client.get_video(VIDEO_ID)
    if video.get("status") != "analyzed":
        raise SystemExit(f"video {VIDEO_ID} must be analyzed first (status={video.get('status')})")
    _process_one({
        "id": video["id"],
        "project_id": video["project_id"],
        "filename": video["filename"],
        "local_source_path": video.get("local_source_path"),
    })


def main() -> None:
    if WORKER_MODE == "single":
        run_single()
    else:
        run_poll()


if __name__ == "__main__":
    main()