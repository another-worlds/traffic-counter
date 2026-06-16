"""Load timestamp-correction artifacts from shared storage."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .storage import (
    get_storage,
    key_timestamp_gaps,
    key_timestamp_region,
    key_timestamp_sync_map,
)

log = logging.getLogger("api.timestamp_gaps")


def key_timestamp_gaps_path(project_id: str, video_id: str) -> str:
    return key_timestamp_gaps(project_id, video_id)


def load_timestamp_status(project_id: str, video_id: str) -> Dict[str, Any]:
    storage = get_storage()
    status_key = f"projects/{project_id}/videos/{video_id}/timestamp_status.json"
    if not storage.exists(status_key):
        return {"status": "pending"}
    with storage.open_read(status_key) as fp:
        return json.load(fp)


def frame_to_wall_epoch(frame_idx: int, sync_map: Dict[str, Any], fps: float) -> Optional[float]:
    """Map frame index to wall-clock epoch using coherence or legacy sync-map."""
    if fps <= 0 or not sync_map:
        return None

    model = sync_map.get("model")
    if model in ("clock_hour_presence_1m", "ideal_day_hour_presence_1m", "ideal_day_vs_detected_1m"):
        ideal_day = sync_map.get("ideal_day") or {}
        map_mode = str(ideal_day.get("map_mode") or "realtime")
        day_start = float(ideal_day.get("day_start_epoch") or 0)
        window_s = float(ideal_day.get("window_s") or 86400)
        video_duration_s = float(sync_map.get("video_duration_s") or 0)
        t_s = frame_idx / fps
        anchor_video_t, anchor_epoch = 0.0, day_start
        for b in sync_map.get("bins", []):
            if b.get("present") and b.get("detected_epoch") is not None:
                anchor_video_t = float(b.get("start_t_s") or 0)
                anchor_epoch = float(b["detected_epoch"])
                break
        for b in sync_map.get("bins", []):
            usable = (
                bool(b.get("present"))
                if model in ("clock_hour_presence_1m", "ideal_day_hour_presence_1m")
                else bool(b.get("coherent"))
            )
            if not usable:
                continue
            if int(b["start_frame"]) <= frame_idx < int(b["end_frame"]):
                if map_mode == "stretch" and video_duration_s > 0:
                    return day_start + t_s * (window_s / video_duration_s)
                return anchor_epoch + (t_s - anchor_video_t)
        return None

    t_s = frame_idx / fps
    for seg in sync_map.get("segments", []):
        start_f = int(seg["start_frame"])
        end_f = int(seg["end_frame"])
        if start_f <= frame_idx < end_f:
            anchor_epoch = float(seg["anchor_epoch"])
            anchor_t_s = float(seg["anchor_t_s"])
            return anchor_epoch + (t_s - anchor_t_s)
    return None


def build_wall_clock_segments_for_tracks(
    tracks_df: pd.DataFrame,
    sync_map: Dict[str, Any],
    fps: float,
) -> List[Dict[str, Any]]:
    """Group trusted track rows into UTC hour segments for corrected XLSX export."""
    if tracks_df is None or tracks_df.empty or not sync_map or fps <= 0:
        return []

    epochs = [frame_to_wall_epoch(int(f), sync_map, fps) for f in tracks_df["frame_idx"].tolist()]
    work = tracks_df.copy()
    work["_wall_epoch"] = epochs
    work = work[work["_wall_epoch"].notna()]
    if work.empty:
        return []

    work["_hour_epoch"] = (work["_wall_epoch"] // 3600).astype("int64") * 3600
    segments: List[Dict[str, Any]] = []
    for hour_epoch, grp in work.groupby("_hour_epoch", sort=True):
        dt = datetime.fromtimestamp(int(hour_epoch), tz=timezone.utc)
        f0 = int(grp["frame_idx"].min())
        f1 = int(grp["frame_idx"].max()) + 1
        segments.append({
            "segment_idx": len(segments),
            "label": dt.strftime("%Y-%m-%d %H:00 UTC"),
            "hour_start_epoch": int(hour_epoch),
            "start_frame": f0,
            "end_frame": f1,
            "start_time_s": f0 / fps,
            "end_time_s": f1 / fps,
        })
    return segments


def load_gap_map(project_id: str, video_id: str) -> Optional[Dict[str, Any]]:
    storage = get_storage()
    gaps_key = key_timestamp_gaps(project_id, video_id)
    if not storage.exists(gaps_key):
        return None
    with storage.open_read(gaps_key) as fp:
        gaps_doc = json.load(fp)
    region = None
    region_key = key_timestamp_region(project_id, video_id)
    if storage.exists(region_key):
        with storage.open_read(region_key) as fp:
            region = json.load(fp)
    sync_map = None
    sync_key = key_timestamp_sync_map(project_id, video_id)
    if storage.exists(sync_key):
        with storage.open_read(sync_key) as fp:
            sync_map = json.load(fp)
    return {
        "region": region,
        "gaps": gaps_doc.get("gaps", []),
        "stats": gaps_doc.get("stats", {}),
        "wall_clock_buckets": gaps_doc.get("wall_clock_buckets") or (sync_map or {}).get("wall_clock_buckets"),
        "hour_presence": gaps_doc.get("hour_presence") or (sync_map or {}).get("hour_presence"),
        "hour_coherence": gaps_doc.get("hour_coherence") or (sync_map or {}).get("hour_coherence"),
        "ideal_day_hours": gaps_doc.get("ideal_day_hours") or (sync_map or {}).get("ideal_day_hours"),
        "clock_hour_video_coverage": (
            gaps_doc.get("clock_hour_video_coverage") or (sync_map or {}).get("clock_hour_video_coverage")
        ),
        "ideal_day": gaps_doc.get("ideal_day") or (sync_map or {}).get("ideal_day"),
        "num_segments": gaps_doc.get("num_segments") or (sync_map or {}).get("num_segments"),
        "sync_map": sync_map,
    }


def filter_tracks_by_gaps(tracks_df: pd.DataFrame, gaps: List[Dict]) -> Tuple[pd.DataFrame, int]:
    """Return (filtered_df, rows_excluded)."""
    if tracks_df is None or tracks_df.empty or not gaps:
        return tracks_df, 0
    gap_frames = set()
    for g in gaps:
        for f in range(int(g["start_frame"]), int(g["end_frame"])):
            gap_frames.add(f)
    before = len(tracks_df)
    filtered = tracks_df[~tracks_df["frame_idx"].isin(gap_frames)].copy()
    return filtered, before - len(filtered)