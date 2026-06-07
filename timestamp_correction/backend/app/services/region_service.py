"""Persist and preview manually drawn timestamp OSD regions."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import numpy as np

from .gap_map import persist_scan
from ..storage import (
    get_storage,
    key_timestamp_region,
    key_timestamp_region_preview,
)
from worker.region_locator import Region
from worker.roi import crop_region, region_area_fraction, validate_region_size

log = logging.getLogger("timestamp_correction.region_service")


def capture_source_preview_bgr(
    project_id: str,
    video_id: str,
    filename: str,
    local_source_path: Optional[str] = None,
) -> tuple[np.ndarray, int, int]:
    """Decode one frame from the video source for unanalyzed / no-keyframe videos."""
    from worker.pipeline import _resolve_video_path

    tmp_path: Optional[str] = None
    try:
        video_path = _resolve_video_path(project_id, video_id, filename, local_source_path)
        if not local_source_path:
            tmp_path = video_path
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError("cannot open video source")
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            raise ValueError("could not decode a preview frame from source video")
        h, w = frame.shape[:2]
        return frame, w, h
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def _keyframe_storage_key(project_id: str, video_id: str, frame_index: int) -> str:
    return f"projects/{project_id}/videos/{video_id}/frames/{frame_index}.jpg"


def _read_keyframe_bgr(project_id: str, video_id: str, frame_index: int) -> Optional[np.ndarray]:
    storage = get_storage()
    key = _keyframe_storage_key(project_id, video_id, frame_index)
    if not storage.exists(key):
        return None
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        storage.download_to(key, tmp.name)
        bgr = cv2.imread(tmp.name)
        Path(tmp.name).unlink(missing_ok=True)
    return bgr


def _write_preview(project_id: str, video_id: str, crop: np.ndarray) -> None:
    storage = get_storage()
    preview_key = key_timestamp_region_preview(project_id, video_id)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        cv2.imwrite(tmp.name, crop, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        storage.upload_file(preview_key, tmp.name)
        Path(tmp.name).unlink(missing_ok=True)


def load_region(project_id: str, video_id: str) -> Optional[Dict[str, Any]]:
    storage = get_storage()
    key = key_timestamp_region(project_id, video_id)
    if not storage.exists(key):
        return None
    return storage.read_json(key)


def delete_region(project_id: str, video_id: str) -> bool:
    storage = get_storage()
    key = key_timestamp_region(project_id, video_id)
    if not storage.exists(key):
        return False
    doc = storage.read_json(key)
    if not str(doc.get("method", "")).startswith("manual"):
        return False
    storage.delete(key)
    preview_key = key_timestamp_region_preview(project_id, video_id)
    if storage.exists(preview_key):
        storage.delete(preview_key)
    persist_scan(video_id, {"region": None})
    return True


def save_manual_region(
    project_id: str,
    video_id: str,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    source_frame_index: Optional[int] = None,
    video_width: Optional[int] = None,
    video_height: Optional[int] = None,
    filename: Optional[str] = None,
    local_source_path: Optional[str] = None,
) -> Dict[str, Any]:
    storage = get_storage()
    ref_bgr: Optional[np.ndarray] = None
    if source_frame_index is not None:
        ref_bgr = _read_keyframe_bgr(project_id, video_id, source_frame_index)

    if ref_bgr is not None:
        fh, fw = ref_bgr.shape[:2]
        video_width = video_width or fw
        video_height = video_height or fh
    elif filename:
        ref_bgr, fw, fh = capture_source_preview_bgr(
            project_id, video_id, filename, local_source_path,
        )
        video_width = video_width or fw
        video_height = video_height or fh
    elif not video_width or not video_height:
        raise ValueError("video_width and video_height required when no preview frame is available")

    region = Region(x=x, y=y, w=w, h=h, confidence=1.0, method="manual")
    region = validate_region_size(region, video_width, video_height)
    area_pct = round(100 * region_area_fraction(region, video_width, video_height), 2)

    if ref_bgr is not None:
        preview_crop = crop_region(ref_bgr, region)
        if preview_crop.size > 0:
            _write_preview(project_id, video_id, preview_crop)

    payload = {
        "x": region.x,
        "y": region.y,
        "w": region.w,
        "h": region.h,
        "confidence": region.confidence,
        "method": region.method,
        "video_width": video_width,
        "video_height": video_height,
        "area_percent": area_pct,
        "source_frame_index": source_frame_index,
        "processing_mode": "roi_only",
    }
    storage.write_json(key_timestamp_region(project_id, video_id), payload)
    persist_scan(video_id, {"region": payload})
    log.info(
        "manual timestamp region for %s: (%d,%d,%d,%d) frame=%s",
        video_id, region.x, region.y, region.w, region.h, source_frame_index,
    )
    return payload