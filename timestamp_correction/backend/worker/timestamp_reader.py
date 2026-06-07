"""
OCR-based timestamp extraction from a located ROI.
"""
from __future__ import annotations

import logging
import re
import time
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

        from app.config import settings
        from app.device import ocr_use_gpu, resolve_torch_device

        use_gpu = ocr_use_gpu(settings.device)
        resolved = resolve_torch_device(settings.device)
        log.info("initializing EasyOCR (gpu=%s, device=%s)", use_gpu, resolved)
        _reader = easyocr.Reader(["en"], gpu=use_gpu, verbose=False)
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
    from .timestamp_parse import parse_osd_datetime

    epoch, _date, time_part = parse_osd_datetime(text)
    if epoch is not None:
        return epoch
    if time_part is not None:
        now = datetime.now(timezone.utc)
        try:
            dt = datetime(
                now.year, now.month, now.day,
                time_part[0], time_part[1], time_part[2] if len(time_part) > 2 else 0,
                tzinfo=timezone.utc,
            )
            return dt.timestamp()
        except ValueError:
            pass
    return None


def read_timestamp_from_crop(bgr: np.ndarray) -> Tuple[Optional[float], str]:
    """Run OCR on a pre-cropped ROI only — never pass a full frame here."""
    if bgr.size == 0:
        return None, ""
    reader = get_ocr_reader()
    texts: List[str] = []
    for img in (bgr, _preprocess_roi(bgr)):
        results = reader.readtext(img, detail=0, paragraph=False)
        texts.extend(str(r).strip() for r in results if str(r).strip())
    # Deduplicate while preserving order.
    seen: set[str] = set()
    merged: List[str] = []
    for t in texts:
        if t not in seen:
            seen.add(t)
            merged.append(t)
    text = " ".join(merged).strip()
    if not text:
        return None, ""
    epoch = _parse_datetime(text)
    return epoch, text


def ocr_success_score(bgr: np.ndarray) -> float:
    """Region-locator score — only timestamp-shaped OCR counts."""
    return score_timestamp_crop(bgr)


def _append_sample(
    samples: List[TimestampSample],
    frame_idx: int,
    fps: float,
    crop: np.ndarray,
) -> None:
    epoch, text = read_timestamp_from_crop(crop)
    samples.append(TimestampSample(
        frame_idx=frame_idx,
        t_seconds=frame_idx / fps if fps > 0 else 0.0,
        wall_clock_epoch=epoch,
        ocr_text=text,
        present=epoch is not None,
    ))


def _sample_timeline_seek(
    cap,
    region,
    fps: float,
    interval_frames: int,
    total_frames: int,
    expected_samples: int,
    progress_cb: Optional[Callable[[int, int, int], None]],
    stats_out: Optional[dict],
    should_cancel: Optional[Callable[[], None]],
) -> List[TimestampSample]:
    """Decode only sample frames via seek — O(samples) not O(total_frames)."""
    from .roi import crop_region
    from .video_decode import seek_to_frame

    samples: List[TimestampSample] = []
    decode_errors = 0
    seek_errors = 0
    t0 = time.perf_counter()

    for sample_i in range(expected_samples):
        if should_cancel:
            should_cancel()

        frame_idx = min(sample_i * interval_frames, max(0, total_frames - 1))
        actual = seek_to_frame(cap, frame_idx, fps)
        if abs(actual - frame_idx) > interval_frames // 2 and sample_i > 0:
            seek_errors += 1

        ok, frame = cap.read()
        if not ok or frame is None:
            decode_errors += 1
            samples.append(TimestampSample(
                frame_idx=frame_idx,
                t_seconds=frame_idx / fps if fps > 0 else 0.0,
                wall_clock_epoch=None,
                ocr_text="",
                present=False,
            ))
        else:
            crop = crop_region(frame, region)
            _append_sample(samples, frame_idx, fps, crop)

        if progress_cb:
            progress_cb(sample_i + 1, expected_samples, frame_idx)

    elapsed = time.perf_counter() - t0
    if stats_out is not None:
        stats_out["decode_errors"] = decode_errors
        stats_out["seek_errors"] = seek_errors
        stats_out["frames_decoded"] = expected_samples
        stats_out["decode_error_fraction"] = round(
            decode_errors / max(expected_samples, 1), 4,
        )
        stats_out["sample_mode"] = "seek"
        stats_out["ocr_elapsed_s"] = round(elapsed, 2)

    log.info(
        "seek timeline OCR: %d samples in %.1fs (decode_errors=%d, seek_errors=%d)",
        len(samples), elapsed, decode_errors, seek_errors,
    )
    return samples


def _sample_timeline_sequential(
    cap,
    region,
    fps: float,
    interval_frames: int,
    total_frames: int,
    expected_samples: int,
    progress_cb: Optional[Callable[[int, int, int], None]],
    stats_out: Optional[dict],
    should_cancel: Optional[Callable[[], None]],
) -> List[TimestampSample]:
    """Walk every frame with grab() between samples — slow but HEVC-stitch safe."""
    from .roi import read_frame_roi

    samples: List[TimestampSample] = []
    sample_i = 0
    decode_errors = 0
    consecutive_failures = 0
    max_consecutive_failures = 30
    frame_idx = 0
    t0 = time.perf_counter()

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

    elapsed = time.perf_counter() - t0
    if stats_out is not None:
        stats_out["decode_errors"] = decode_errors
        stats_out["frames_decoded"] = frame_idx
        stats_out["decode_error_fraction"] = round(
            decode_errors / max(frame_idx, 1), 4,
        )
        stats_out["sample_mode"] = "sequential"
        stats_out["ocr_elapsed_s"] = round(elapsed, 2)

    log.info(
        "sequential timeline OCR: %d samples, %d frames walked in %.1fs (decode_errors=%d)",
        len(samples), frame_idx, elapsed, decode_errors,
    )
    return samples


def sample_timeline(
    cap,
    region,
    fps: float,
    sample_interval_s: float,
    total_frames: int,
    progress_cb: Optional[Callable[[int, int, int], None]] = None,
    stats_out: Optional[dict] = None,
    should_cancel: Optional[Callable[[], None]] = None,
    sample_mode: str = "seek",
) -> List[TimestampSample]:
    """Sample timestamps every ``sample_interval_s`` seconds of video time.

    ``seek`` mode decodes only sample frames (fast). ``sequential`` walks the
    full file with grab() between samples (slow; use for problematic HEVC).
    """
    interval_frames = compute_sample_stride_frames(fps, sample_interval_s)
    expected_samples = max(1, (total_frames + interval_frames - 1) // interval_frames)

    log.info(
        "timeline OCR: mode=%s stride=%d frames (%.1fs at %.2f fps) → ~%d samples / %d total frames",
        sample_mode, interval_frames, sample_interval_s, fps, expected_samples, total_frames,
    )

    if sample_mode == "sequential":
        return _sample_timeline_sequential(
            cap, region, fps, interval_frames, total_frames, expected_samples,
            progress_cb, stats_out, should_cancel,
        )
    return _sample_timeline_seek(
        cap, region, fps, interval_frames, total_frames, expected_samples,
        progress_cb, stats_out, should_cancel,
    )