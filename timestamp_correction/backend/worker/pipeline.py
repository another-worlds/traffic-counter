"""
Timestamp correction worker pipeline.

1. Open source video
2. Locate OSD region (CNN + OCR bootstrap)
3. Sample timeline via OCR
4. Detect gaps
5. Write artifacts to shared storage
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

import cv2
import pandas as pd

from app import counter_client
from app.config import settings
from app.services.gap_map import _artifact_flags, persist_scan, write_video_index
from app.services.scan_control import ScanCancelled, check_cancelled, merge_processing_status
from app.services.timeline_viz import build_timeline_visualization
from app.storage import (
    get_storage,
    key_timestamp_gaps,
    key_timestamp_region,
    key_timestamp_region_preview,
    key_timestamp_status,
    key_timestamp_sync_map,
    key_timestamp_timeline,
)

from .gap_detector import detect_gaps, gap_stats
from .sync_map import build_sync_map
from .region_locator import Region, locate_region
from .roi import crop_region, region_area_fraction, tighten_region, validate_region_size
from .timestamp_reader import (
    compute_sample_stride_frames,
    ocr_success_score,
    sample_timeline,
)
from .video_decode import configure_quiet_decode

log = logging.getLogger("timestamp_correction.pipeline")

REGION_SAMPLE_COUNT = int(os.environ.get("REGION_SAMPLE_COUNT", "12"))

PHASES = {
    "opening": ("Opening video", 0.0, 0.05),
    "locating": ("Detecting timestamp region", 0.05, 0.20),
    "ocr": ("Reading timestamps (OCR)", 0.20, 0.70),
    "gaps": ("Analyzing coherence & gaps", 0.70, 0.90),
    "finalize": ("Saving results", 0.90, 1.0),
}


def _resolve_video_fps(cap, video_id: str) -> float:
    """Per-video FPS for minute→frame stride (container metadata, then counter API)."""
    raw = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    if raw > 1.0:
        return raw
    try:
        meta = counter_client.get_video(video_id, timeout=5.0)
        api_fps = float(meta.get("fps") or 0.0)
        if api_fps > 1.0:
            log.info("using counter API fps=%.3f for %s (container reported %.3f)", api_fps, video_id, raw)
            return api_fps
    except Exception:
        pass
    from .timestamp_reader import DEFAULT_FPS
    log.warning("fps unknown for %s — defaulting to %.1f", video_id, DEFAULT_FPS)
    return DEFAULT_FPS


def _resolve_video_path(
    project_id: str,
    video_id: str,
    filename: str,
    local_source_path: Optional[str],
) -> str:
    if local_source_path and Path(local_source_path).exists():
        return local_source_path
    storage = get_storage()
    ext = Path(filename).suffix or ".mp4"
    key = f"projects/{project_id}/videos/{video_id}/source{ext}"
    if not storage.exists(key):
        raise FileNotFoundError(f"video source not found: {key}")
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        storage.download_to(key, tmp.name)
        return tmp.name


def _sample_frames_for_locator(
    cap,
    total_frames: int,
    n: int,
    should_cancel: Optional[Callable[[], None]] = None,
) -> list:
    """Sequential stride sampling — more reliable on stitched HEVC than random seeks."""
    frames = []
    if total_frames <= 0:
        return frames
    stride = max(1, total_frames // max(n, 1))
    frame_idx = 0
    while len(frames) < n and frame_idx < total_frames:
        if should_cancel:
            should_cancel()
        ok, frame = cap.read()
        if not ok:
            break
        if frame_idx % stride == 0:
            frames.append(frame)
        frame_idx += 1
    return frames


def _load_manual_region(project_id: str, video_id: str) -> Optional[Region]:
    storage = get_storage()
    key = key_timestamp_region(project_id, video_id)
    if not storage.exists(key):
        return None
    doc = storage.read_json(key)
    method = str(doc.get("method", ""))
    if not method.startswith("manual"):
        return None
    return Region(
        x=int(doc["x"]),
        y=int(doc["y"]),
        w=int(doc["w"]),
        h=int(doc["h"]),
        confidence=float(doc.get("confidence", 1.0)),
        method=method,
    )


def _region_from_override(data: Dict) -> Region:
    return Region(
        x=int(data["x"]),
        y=int(data["y"]),
        w=int(data["w"]),
        h=int(data["h"]),
        confidence=float(data.get("confidence", 1.0)),
        method=str(data.get("method", "manual")),
    )


def process_video(
    project_id: str,
    video_id: str,
    filename: str,
    local_source_path: Optional[str] = None,
    progress_cb: Optional[Callable[[float], None]] = None,
    region_override: Optional[Dict] = None,
) -> dict:
    configure_quiet_decode()
    storage = get_storage()
    status_key = key_timestamp_status(project_id, video_id)
    started_at = datetime.now(timezone.utc).isoformat()

    def _write_status(phase: str, sub_pct: float = 0.0, message: Optional[str] = None) -> None:
        label, lo, hi = PHASES.get(phase, (phase, 0.0, 1.0))
        pct = round((lo + (hi - lo) * max(0.0, min(1.0, sub_pct))) * 100, 1)
        payload = {
            "status": "processing",
            "started_at": started_at,
            "video_id": video_id,
            "project_id": project_id,
            "progress": {
                "phase": phase,
                "phase_label": label,
                "percent": pct,
                "message": message,
            },
            "artifacts": _artifact_flags(project_id, video_id),
        }
        payload = merge_processing_status(project_id, video_id, payload)
        storage.write_json(status_key, payload)
        persist_scan(video_id, payload)
        if progress_cb:
            progress_cb(pct / 100.0)

    write_video_index(project_id, video_id, filename, local_source_path)
    _write_status("opening", 0.0, "Opening source video")

    tmp_path: Optional[str] = None
    try:
        video_path = _resolve_video_path(project_id, video_id, filename, local_source_path)
        if not local_source_path:
            tmp_path = video_path

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"cannot open video: {video_path}")

        fps = _resolve_video_fps(cap, video_id)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        stride_frames = compute_sample_stride_frames(fps, settings.sample_interval_s)
        log.info(
            "video %s: fps=%.3f stride=%d frames (%.1f min interval)",
            video_id, fps, stride_frames, settings.sample_interval_minutes,
        )
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

        manual_region = (
            _region_from_override(region_override)
            if region_override
            else _load_manual_region(project_id, video_id)
        )

        if manual_region is not None:
            _write_status("locating", 0.2, "Using saved manual timestamp region")
            region = manual_region
            ref_frame = None
            if manual_region.method.startswith("manual"):
                region_doc = storage.read_json(key_timestamp_region(project_id, video_id))
                kf_idx = int(region_doc.get("source_frame_index") or 0)
                keyframe_key = f"projects/{project_id}/videos/{video_id}/frames/{kf_idx}.jpg"
                if storage.exists(keyframe_key):
                    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                        storage.download_to(keyframe_key, tmp.name)
                        ref_frame = cv2.imread(tmp.name)
                        Path(tmp.name).unlink(missing_ok=True)
            if ref_frame is None:
                ok, ref_frame = cap.read()
                if not ok:
                    raise RuntimeError("could not read reference frame for manual region")
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            region = tighten_region(ref_frame, region)
            region = validate_region_size(region, width, height)
        else:
            _write_status("locating", 0.1, "Sampling frames for OSD detection")
            cancel_cb = lambda: check_cancelled(project_id, video_id)
            locator_frames = _sample_frames_for_locator(
                cap, total_frames, REGION_SAMPLE_COUNT, should_cancel=cancel_cb,
            )
            if not locator_frames:
                raise RuntimeError("could not read any frames for region location")

            check_cancelled(project_id, video_id)
            region = locate_region(
                locator_frames,
                weights_path=settings.region_locator_weights,
                device=settings.device,
                ocr_scorer=ocr_success_score,
            )
            ref_frame = locator_frames[len(locator_frames) // 2]
            region = tighten_region(ref_frame, region)
            region = validate_region_size(region, width, height)
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        preview_crop = crop_region(ref_frame, region)
        preview_key = key_timestamp_region_preview(project_id, video_id)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            cv2.imwrite(tmp.name, preview_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
            storage.upload_file(preview_key, tmp.name)
            Path(tmp.name).unlink(missing_ok=True)

        area_pct = round(100 * region_area_fraction(region, width, height), 2)
        storage.write_json(key_timestamp_region(project_id, video_id), {
            "x": region.x, "y": region.y, "w": region.w, "h": region.h,
            "confidence": region.confidence,
            "method": region.method,
            "video_width": width,
            "video_height": height,
            "area_percent": area_pct,
            "preview_key": preview_key,
            "processing_mode": "roi_only",
        })
        log.info(
            "timestamp ROI for %s: (%d,%d,%d,%d) = %.2f%% of frame, preview saved",
            video_id, region.x, region.y, region.w, region.h, area_pct,
        )
        _write_status("locating", 1.0, f"Region locked ({region.w}×{region.h}px)")

        def _ocr_progress(done: int, total: int, frame_idx: int) -> None:
            sub = done / max(total, 1)
            _write_status(
                "ocr",
                sub,
                f"OCR sample {done}/{total} · frame {frame_idx}",
            )

        _write_status("ocr", 0.0, "Starting timeline OCR")
        decode_stats: dict = {}
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        samples = sample_timeline(
            cap,
            region,
            fps=fps,
            sample_interval_s=settings.sample_interval_s,
            total_frames=total_frames,
            progress_cb=_ocr_progress,
            stats_out=decode_stats,
            should_cancel=lambda: check_cancelled(project_id, video_id),
        )
        cap.release()

        _write_status("gaps", 0.2, f"Scanned {len(samples)} timeline points")
        timeline_df = pd.DataFrame([
            {
                "frame_idx": s.frame_idx,
                "t_seconds": s.t_seconds,
                "wall_clock_epoch": s.wall_clock_epoch,
                "ocr_text": s.ocr_text,
                "present": s.present,
            }
            for s in samples
        ])
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as tmp:
            timeline_df.to_parquet(tmp.name, index=False)
            storage.upload_file(key_timestamp_timeline(project_id, video_id), tmp.name)
            Path(tmp.name).unlink(missing_ok=True)

        sync_map_doc = None
        if settings.use_sync_map:
            sync_map_doc = build_sync_map(
                samples,
                fps=fps,
                total_frames=total_frames,
                bin_duration_s=settings.sync_bin_duration_s,
                sync_tolerance_s=settings.sync_tolerance_s,
                stable_bins_required=settings.sync_stable_bins_required,
            )
            storage.write_json(key_timestamp_sync_map(project_id, video_id), sync_map_doc)
            stats = dict(sync_map_doc.get("stats") or {})
            gap_list = list(sync_map_doc.get("gaps") or [])
        else:
            gaps = detect_gaps(
                samples,
                fps=fps,
                sample_interval_s=settings.sample_interval_s,
                min_gap_samples=settings.min_gap_samples,
                jump_threshold_s=settings.jump_threshold_s,
                frozen_threshold_s=settings.frozen_threshold_s,
            )
            stats = gap_stats(gaps, total_frames, fps)
            gap_list = [
                {
                    "start_frame": g.start_frame,
                    "end_frame": g.end_frame,
                    "start_t_s": g.start_t_s,
                    "end_t_s": g.end_t_s,
                    "reason": g.reason,
                }
                for g in gaps
            ]

        stats["fps"] = round(fps, 3)
        stats["sample_interval_minutes"] = settings.sample_interval_minutes
        stats["sample_interval_s"] = settings.sample_interval_s
        stats["sample_stride_frames"] = stride_frames
        stats["sync_map_enabled"] = settings.use_sync_map
        if decode_stats:
            stats["decode_errors"] = decode_stats.get("decode_errors", 0)
            stats["decode_error_fraction"] = decode_stats.get("decode_error_fraction", 0.0)
        gaps_payload = {
            "gaps": gap_list,
            "stats": stats,
        }
        if sync_map_doc:
            gaps_payload["wall_clock_buckets"] = sync_map_doc.get("wall_clock_buckets", [])
            gaps_payload["num_segments"] = sync_map_doc.get("num_segments", 0)
        storage.write_json(key_timestamp_gaps(project_id, video_id), gaps_payload)

        num_gaps = int(stats.get("num_gaps") or len(gap_list))
        _write_status("finalize", 0.8, f"Found {num_gaps} gap fragment(s)")
        region_payload = {
            "x": region.x, "y": region.y, "w": region.w, "h": region.h,
            "confidence": region.confidence,
            "method": region.method,
            "video_width": width,
            "video_height": height,
            "area_percent": area_pct,
            "processing_mode": "roi_only",
        }
        timeline_summary = {
            "num_samples": len(timeline_df),
            "present_fraction": round(float(timeline_df["present"].mean()), 4) if len(timeline_df) else 0.0,
            "first_t_s": float(timeline_df["t_seconds"].iloc[0]) if len(timeline_df) else None,
            "last_t_s": float(timeline_df["t_seconds"].iloc[-1]) if len(timeline_df) else None,
        }
        timeline_viz = build_timeline_visualization(
            gaps_payload["gaps"], stats, timeline_df,
        )
        result = {
            "status": "done",
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "region": region_payload,
            "gaps": gap_list,
            "stats": stats,
            "timeline_summary": timeline_summary,
            "timeline_viz": timeline_viz,
            "progress": {
                "phase": "done",
                "phase_label": "Complete",
                "percent": 100.0,
                "message": f"{num_gaps} gap fragment(s) detected",
            },
            "artifacts": _artifact_flags(project_id, video_id),
        }
        storage.write_json(status_key, result)
        persist_scan(video_id, result)
        log.info("timestamp scan done for %s: %d gaps, %.1f%% gap fraction",
                 video_id, num_gaps, 100 * float(stats.get("gap_fraction") or 0))
        return result

    except ScanCancelled:
        log.info("timestamp scan cancelled for %s", video_id)
        if storage.exists(status_key):
            return storage.read_json(status_key)
        return {"status": "cancelled", "video_id": video_id, "project_id": project_id}
    except Exception as exc:
        log.exception("timestamp scan failed for %s", video_id)
        err_payload = {
            "status": "error",
            "error_message": str(exc),
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        storage.write_json(status_key, err_payload)
        persist_scan(video_id, err_payload)
        raise
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)