"""
OCR-based timestamp extraction from a located ROI.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger("timestamp_correction.reader")

DEFAULT_FPS = 25.0


def compute_sample_stride_frames(fps: float, sample_interval_s: float) -> int:
    """Per-video frame stride: one OCR sample every `sample_interval_s` seconds of footage."""
    effective_fps = fps if fps and fps > 0 else DEFAULT_FPS
    return max(1, int(round(effective_fps * sample_interval_s)))

# Common CCTV OSD patterns
_PATTERNS = [
    re.compile(r"(\d{4})[-/.](\d{2})[-/.](\d{2})\s+(\d{2}):(\d{2}):(\d{2})"),
    re.compile(r"(\d{2})[-/.](\d{2})[-/.](\d{4})\s+(\d{2}):(\d{2}):(\d{2})"),
    re.compile(r"(\d{2}):(\d{2}):(\d{2})"),
]

_reader = None


def get_ocr_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _reader


@dataclass
class TimestampSample:
    frame_idx: int
    t_seconds: float
    wall_clock_epoch: Optional[float]
    ocr_text: str
    present: bool


def _preprocess_roi(bgr: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    gray = cv2.equalizeHist(gray)
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


_TIMESTAMP_MIN_SCORE = 0.55


def score_text_timestamp_likeness(text: str) -> float:
    """Rank OCR snippets — full datetimes score highest, plain words score zero."""
    cleaned = re.sub(r"[^\d/:.\-\s]", "", text).strip()
    if not cleaned:
        return 0.0
    if re.search(r"[a-zA-Z]{3,}", text):
        return 0.0
    if _parse_datetime(cleaned) is not None:
        return 1.0
    if re.search(r"\d{4}[-/.]\d{2}[-/.]\d{2}", cleaned):
        return 0.85
    if re.search(r"\d{2}[-/.]\d{2}[-/.]\d{4}", cleaned):
        return 0.85
    if re.search(r"\d{2}:\d{2}:\d{2}", cleaned):
        return 0.75
    digits = sum(ch.isdigit() for ch in cleaned)
    ratio = digits / max(len(cleaned), 1)
    if ratio >= 0.5 and (":" in cleaned or "-" in cleaned or "/" in cleaned):
        return 0.35
    return 0.0


def score_timestamp_crop(bgr: np.ndarray) -> float:
    """Best timestamp-format score across OCR lines in a crop."""
    if bgr.size == 0:
        return 0.0
    processed = _preprocess_roi(bgr)
    reader = get_ocr_reader()
    results = reader.readtext(processed, detail=1, paragraph=False)
    best = 0.0
    for _bbox, text, conf in results:
        if conf < 0.1:
            continue
        best = max(best, score_text_timestamp_likeness(str(text)))
    return best


def _parse_datetime(text: str) -> Optional[float]:
    cleaned = re.sub(r"[^\d/:.\-\s]", "", text).strip()
    for pat in _PATTERNS:
        m = pat.search(cleaned)
        if not m:
            continue
        groups = m.groups()
        try:
            if len(groups) == 6:
                if len(groups[0]) == 4:
                    dt = datetime(
                        int(groups[0]), int(groups[1]), int(groups[2]),
                        int(groups[3]), int(groups[4]), int(groups[5]),
                        tzinfo=timezone.utc,
                    )
                else:
                    dt = datetime(
                        int(groups[2]), int(groups[1]), int(groups[0]),
                        int(groups[3]), int(groups[4]), int(groups[5]),
                        tzinfo=timezone.utc,
                    )
            else:
                now = datetime.now(timezone.utc)
                dt = datetime(
                    now.year, now.month, now.day,
                    int(groups[0]), int(groups[1]), int(groups[2]),
                    tzinfo=timezone.utc,
                )
            return dt.timestamp()
        except ValueError:
            continue
    return None


def read_timestamp_from_crop(bgr: np.ndarray) -> Tuple[Optional[float], str]:
    """Run OCR on a pre-cropped ROI only — never pass a full frame here."""
    if bgr.size == 0:
        return None, ""
    processed = _preprocess_roi(bgr)
    reader = get_ocr_reader()
    results = reader.readtext(processed, detail=0, paragraph=True)
    text = " ".join(results).strip()
    if not text:
        return None, ""
    epoch = _parse_datetime(text)
    return epoch, text


def ocr_success_score(bgr: np.ndarray) -> float:
    """Region-locator score — only timestamp-shaped OCR counts."""
    return score_timestamp_crop(bgr)


def sample_timeline(
    cap,
    region,
    fps: float,
    sample_interval_s: float,
    total_frames: int,
    progress_cb: Optional[Callable[[int, int, int], None]] = None,
    stats_out: Optional[dict] = None,
    should_cancel: Optional[Callable[[], None]] = None,
) -> List[TimestampSample]:
    """Walk the video forward, OCR-ing only the timestamp ROI every `sample_interval_s` seconds.

    Sequential decode avoids broken HEVC reference chains from random seeks.
    """
    from .roi import read_frame_roi

    interval_frames = compute_sample_stride_frames(fps, sample_interval_s)
    expected_samples = max(1, (total_frames + interval_frames - 1) // interval_frames)
    samples: List[TimestampSample] = []
    sample_i = 0
    decode_errors = 0
    consecutive_failures = 0
    max_consecutive_failures = 30
    frame_idx = 0

    log.info(
        "timeline OCR stride: every %d frames (%.1fs at %.2f fps)",
        interval_frames, sample_interval_s, fps,
    )

    while frame_idx < total_frames:
        if should_cancel:
            should_cancel()

        is_sample = frame_idx % interval_frames == 0
        if is_sample:
            ok, crop = read_frame_roi(cap, region)
        else:
            ok = cap.grab()
            crop = None

        if not ok:
            decode_errors += 1
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                log.warning(
                    "stopping timeline OCR after %d consecutive decode failures at frame %d",
                    consecutive_failures, frame_idx,
                )
                break
            frame_idx += 1
            continue
        consecutive_failures = 0

        if is_sample and crop is not None:
            epoch, text = read_timestamp_from_crop(crop)
            samples.append(TimestampSample(
                frame_idx=frame_idx,
                t_seconds=frame_idx / fps if fps > 0 else 0.0,
                wall_clock_epoch=epoch,
                ocr_text=text,
                present=epoch is not None,
            ))
            sample_i += 1
            if progress_cb:
                progress_cb(sample_i, expected_samples, frame_idx)

        frame_idx += 1

    if stats_out is not None:
        stats_out["decode_errors"] = decode_errors
        stats_out["frames_decoded"] = frame_idx
        stats_out["decode_error_fraction"] = round(
            decode_errors / max(frame_idx, 1), 4,
        )

    log.info(
        "sampled %d timeline points (interval=%.1fs, decode_errors=%d)",
        len(samples), sample_interval_s, decode_errors,
    )
    return samples