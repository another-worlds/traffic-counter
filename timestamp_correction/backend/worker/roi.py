"""
Timestamp ROI helpers — all OCR and persisted imagery use the OSD crop only,
never full video frames.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

import cv2
import numpy as np

from .region_locator import Region

log = logging.getLogger("timestamp_correction.roi")

# Reject a bbox covering more than this fraction of the frame — OSD is always small.
MAX_REGION_AREA_FRACTION = float(
    __import__("os").environ.get("MAX_REGION_AREA_FRACTION", "0.12")
)
MIN_OCR_CONF = 0.15
TIGHTEN_PADDING_PX = 6


def crop_region(frame: np.ndarray, region: Region) -> np.ndarray:
    """Extract the timestamp ROI from a full decoded frame."""
    h, w = frame.shape[:2]
    x0 = max(0, region.x)
    y0 = max(0, region.y)
    x1 = min(w, region.x + region.w)
    y1 = min(h, region.y + region.h)
    if x1 <= x0 or y1 <= y0:
        return np.empty((0, 0, 3), dtype=frame.dtype)
    return frame[y0:y1, x0:x1].copy()


def region_area_fraction(region: Region, frame_w: int, frame_h: int) -> float:
    if frame_w <= 0 or frame_h <= 0:
        return 1.0
    return (region.w * region.h) / (frame_w * frame_h)


def validate_region_size(region: Region, frame_w: int, frame_h: int) -> Region:
    frac = region_area_fraction(region, frame_w, frame_h)
    if frac > MAX_REGION_AREA_FRACTION:
        raise ValueError(
            f"detected timestamp region covers {frac:.1%} of the frame "
            f"(max {MAX_REGION_AREA_FRACTION:.0%}) — likely a false positive"
        )
    return region


def tighten_region(frame: np.ndarray, region: Region) -> Region:
    """Shrink the coarse bbox to the pixels that contain OCR text."""
    crop = crop_region(frame, region)
    if crop.size == 0:
        return region

    from .timestamp_reader import get_ocr_reader

    reader = get_ocr_reader()
    results = reader.readtext(crop, detail=1, paragraph=False)
    if not results:
        return region

    xs: list[float] = []
    ys: list[float] = []
    from .timestamp_reader import score_text_timestamp_likeness

    for bbox, text, conf in results:
        if conf < MIN_OCR_CONF:
            continue
        if score_text_timestamp_likeness(str(text)) < 0.55:
            continue
        pts = np.asarray(bbox, dtype=np.float32)
        xs.extend(pts[:, 0].tolist())
        ys.extend(pts[:, 1].tolist())

    if not xs:
        return region

    pad = TIGHTEN_PADDING_PX
    lx0 = max(0, int(min(xs)) - pad)
    ly0 = max(0, int(min(ys)) - pad)
    lx1 = min(crop.shape[1], int(max(xs)) + pad)
    ly1 = min(crop.shape[0], int(max(ys)) + pad)

    if lx1 - lx0 < 20 or ly1 - ly0 < 10:
        return region

    return Region(
        x=region.x + lx0,
        y=region.y + ly0,
        w=lx1 - lx0,
        h=ly1 - ly0,
        confidence=region.confidence,
        method=f"{region.method}+tightened",
    )


def read_frame_roi(cap, region: Region) -> Tuple[bool, np.ndarray]:
    """Decode one frame and return only the ROI crop (full frame is not retained)."""
    ok, frame = cap.read()
    if not ok:
        return False, np.empty((0, 0, 3), dtype=np.uint8)
    return True, crop_region(frame, region)