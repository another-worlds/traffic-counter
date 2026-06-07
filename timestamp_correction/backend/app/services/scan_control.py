"""Per-video timestamp scan control: cancel in-flight scans and disable auto-queue."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from ..db import delete_scan_row
from ..storage import (
    get_storage,
    key_timestamp_gaps,
    key_timestamp_region,
    key_timestamp_region_preview,
    key_timestamp_status,
    key_timestamp_timeline,
    key_timestamp_sync_map,
)
from .gap_map import _artifact_flags, persist_scan


class ScanCancelled(Exception):
    """Raised when the user requested stop during an in-flight scan."""


class ScanInProgress(Exception):
    """Raised when a destructive action requires the scan to be stopped first."""


def _status_key(project_id: str, video_id: str) -> str:
    return key_timestamp_status(project_id, video_id)


def read_scan_doc(project_id: str, video_id: str) -> Dict[str, Any]:
    storage = get_storage()
    key = _status_key(project_id, video_id)
    if not storage.exists(key):
        return {}
    return storage.read_json(key)


def is_auto_scan_enabled(project_id: str, video_id: str) -> bool:
    doc = read_scan_doc(project_id, video_id)
    return doc.get("auto_scan_enabled", True) is not False


def is_cancel_requested(project_id: str, video_id: str) -> bool:
    doc = read_scan_doc(project_id, video_id)
    return bool(doc.get("cancel_requested"))


def check_cancelled(project_id: str, video_id: str) -> None:
    if is_cancel_requested(project_id, video_id):
        raise ScanCancelled()


def stop_scan(project_id: str, video_id: str) -> Dict[str, Any]:
    """Stop an active or queued scan and disable worker auto-queue for this video."""
    storage = get_storage()
    key = _status_key(project_id, video_id)
    now = datetime.now(timezone.utc).isoformat()
    prev = read_scan_doc(project_id, video_id)
    payload = {
        **prev,
        "status": "cancelled",
        "auto_scan_enabled": False,
        "cancel_requested": True,
        "completed_at": now,
        "error_message": "Stopped by user — set timestamp region manually, then run scan",
        "progress": {
            "phase": "cancelled",
            "phase_label": "Stopped",
            "percent": prev.get("progress", {}).get("percent", 0) if prev.get("progress") else 0,
            "message": "Auto-scan disabled for this video",
        },
        "artifacts": _artifact_flags(project_id, video_id),
        "video_id": video_id,
        "project_id": project_id,
    }
    storage.write_json(key, payload)
    persist_scan(video_id, payload)
    return payload


def merge_processing_status(
    project_id: str,
    video_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Preserve cancel/auto-scan flags while writing processing updates."""
    doc = read_scan_doc(project_id, video_id)
    if doc.get("cancel_requested"):
        raise ScanCancelled()
    if "auto_scan_enabled" in doc:
        payload["auto_scan_enabled"] = doc["auto_scan_enabled"]
    else:
        payload["auto_scan_enabled"] = True
    payload["cancel_requested"] = False
    return payload


def delete_scan(project_id: str, video_id: str) -> Dict[str, Any]:
    """Remove all timestamp analysis artifacts and reset scan state for a video."""
    storage = get_storage()
    prev = read_scan_doc(project_id, video_id)
    if prev.get("status") == "processing":
        raise ScanInProgress("stop the scan before deleting analysis")

    for key in (
        key_timestamp_status(project_id, video_id),
        key_timestamp_region(project_id, video_id),
        key_timestamp_region_preview(project_id, video_id),
        key_timestamp_timeline(project_id, video_id),
        key_timestamp_sync_map(project_id, video_id),
        key_timestamp_gaps(project_id, video_id),
    ):
        if storage.exists(key):
            storage.delete(key)

    delete_scan_row(video_id)

    payload = {
        "status": "pending",
        "auto_scan_enabled": True,
        "cancel_requested": False,
        "video_id": video_id,
        "project_id": project_id,
        "artifacts": _artifact_flags(project_id, video_id),
    }
    storage.write_json(_status_key(project_id, video_id), payload)
    persist_scan(video_id, payload)
    return payload