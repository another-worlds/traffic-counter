"""
Gap-filtered counting — syncs vehicle counts against timestamp presence map.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .. import counter_client
from ..storage import get_storage, key_timestamp_gaps, key_tracks, key_tracks_segment
from ..storage import get_storage, key_timestamp_sync_map
from .gap_map import load_gap_map
from worker.sync_map import frame_to_wall_epoch

_TRACK_ID_SEGMENT_OFFSET = 1_000_000
_DTYPES = {
    "frame_idx": "int32",
    "t_seconds": "float32",
    "track_id": "int32",
    "class_id": "int8",
    "conf": "float32",
    "cx": "float32",
    "cy": "float32",
    "w": "float32",
    "h": "float32",
}

COCO_VEHICLE_CLASSES = {
    1: "bicycle", 2: "car", 3: "motorcycle", 5: "bus", 7: "truck",
}


def _load_tracks(project_id: str, video_id: str) -> pd.DataFrame:
    storage = get_storage()
    frames: List[pd.DataFrame] = []
    seg_idx = 0
    while True:
        key = key_tracks_segment(project_id, video_id, seg_idx)
        if not storage.exists(key):
            break
        with storage.open_read(key) as fp:
            df = pd.read_parquet(fp)
        df = df.astype(_DTYPES, copy=False)
        df["track_id"] = df["track_id"] + seg_idx * _TRACK_ID_SEGMENT_OFFSET
        frames.append(df)
        seg_idx += 1
    if frames:
        return pd.concat(frames, ignore_index=True)
    legacy = key_tracks(project_id, video_id)
    if storage.exists(legacy):
        with storage.open_read(legacy) as fp:
            return pd.read_parquet(fp).astype(_DTYPES, copy=False)
    return pd.DataFrame(columns=list(_DTYPES.keys()))


def _filter_gap_frames(tracks: pd.DataFrame, gaps: List[Dict]) -> pd.DataFrame:
    if tracks.empty or not gaps:
        return tracks
    gap_frames = set()
    for g in gaps:
        for f in range(int(g["start_frame"]), int(g["end_frame"])):
            gap_frames.add(f)
    return tracks[~tracks["frame_idx"].isin(gap_frames)].copy()


def _cross_2d(u, v):
    return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]


def _materialize(tracks_df: pd.DataFrame):
    if tracks_df.empty:
        return [], [], np.empty(0, dtype=np.int16)
    df = tracks_df.sort_values(["track_id", "frame_idx"], kind="mergesort")
    tids, pts_list, modals = [], [], []
    for tid, g in df.groupby("track_id", sort=False):
        pts = g[["cx", "cy"]].to_numpy(dtype=np.float64, copy=True)
        if pts.shape[0] < 2:
            continue
        cls = g["class_id"].to_numpy()
        modal = int(np.bincount(cls.astype(np.int64)).argmax())
        tids.append(int(tid))
        pts_list.append(pts)
        modals.append(modal)
    return tids, pts_list, np.asarray(modals, dtype=np.int16)


def _count_line(tids, pts_list, modals, line_a, line_b) -> Dict:
    A = np.asarray(line_a, dtype=np.float64)
    B = np.asarray(line_b, dtype=np.float64)
    AB = B - A
    by_class, by_dir = {}, {"positive": 0, "negative": 0}
    total = 0
    for i, pts in enumerate(pts_list):
        P, Q = pts[:-1], pts[1:]
        PQ = Q - P
        d1 = np.sign(_cross_2d(AB, P - A))
        d2 = np.sign(_cross_2d(AB, Q - A))
        d3 = np.sign(_cross_2d(PQ, A - P))
        d4 = np.sign(_cross_2d(PQ, B - P))
        intersect = (d1 != d2) & (d3 != d4)
        if not intersect.any():
            continue
        k = int(np.argmax(intersect))
        cls_id = int(modals[i])
        cls_name = COCO_VEHICLE_CLASSES.get(cls_id, f"class_{cls_id}")
        direction = "positive" if _cross_2d(AB, PQ[k]) >= 0 else "negative"
        by_class[cls_name] = by_class.get(cls_name, 0) + 1
        by_dir[direction] += 1
        total += 1
    return {"total": total, "by_class": by_class, "by_direction": by_dir}


def _counts_for_lines(tracks_df: pd.DataFrame, lines: List[Dict]) -> Dict:
    tids, pts_list, modals = _materialize(tracks_df)
    T = len(tids)
    per_line = []
    for ln in lines:
        r = _count_line(tids, pts_list, modals, ln["a"], ln["b"])
        per_line.append({
            "line_id": ln["id"],
            "line_name": ln["name"],
            "total": r["total"],
            "by_class": r["by_class"],
            "by_direction": r["by_direction"],
        })
    sum_across = sum(p["total"] for p in per_line)
    for p in per_line:
        p["percent_of_video_total"] = round(100.0 * p["total"] / T, 2) if T else 0.0
        p["percent_of_drawn_lines"] = round(100.0 * p["total"] / sum_across, 2) if sum_across else 0.0
    return {
        "total_unique_tracks": T,
        "sum_across_lines": sum_across,
        "per_line": per_line,
    }


def _load_sync_map(project_id: str, video_id: str) -> Optional[Dict]:
    storage = get_storage()
    key = key_timestamp_sync_map(project_id, video_id)
    if not storage.exists(key):
        return None
    return storage.read_json(key)


def compute_wall_clock_bucket_counts(
    project_id: str,
    video_id: str,
    line_ids: List[str],
) -> Dict:
    """Count line crossings grouped by mapped UTC hour (requires sync map)."""
    sync_map = _load_sync_map(project_id, video_id)
    if sync_map is None:
        raise ValueError("timestamp sync map not available — run timestamp-scan first")

    gap_data = load_gap_map(project_id, video_id)
    if gap_data is None:
        raise ValueError("timestamp gap map not available — run timestamp-scan first")

    fps = float((gap_data.get("stats") or {}).get("fps") or 0.0)
    if fps <= 0:
        raise ValueError("video fps unavailable in gap stats")

    lines = counter_client.list_lines(video_id)
    selected = [ln for ln in lines if ln["id"] in line_ids]
    if len(selected) != len(line_ids):
        raise ValueError("one or more line_ids do not belong to this video")

    line_dicts = [
        {"id": ln["id"], "name": ln["name"], "a": ln["points"]["a"], "b": ln["points"]["b"]}
        for ln in selected
    ]

    tracks = _load_tracks(project_id, video_id)
    filtered = _filter_gap_frames(tracks, gap_data["gaps"])
    if filtered.empty:
        return {
            "buckets": [],
            "wall_clock_buckets": sync_map.get("wall_clock_buckets", []),
            "rows_used": 0,
        }

    filtered = filtered.copy()
    epochs = [
        frame_to_wall_epoch(int(f), sync_map, fps)
        for f in filtered["frame_idx"].tolist()
    ]
    filtered["_wall_epoch"] = epochs
    filtered = filtered[filtered["_wall_epoch"].notna()]

    buckets_out = []
    if not filtered.empty:
        filtered["_hour"] = (filtered["_wall_epoch"] // 3600).astype(int) * 3600
        for hour_epoch, group in filtered.groupby("_hour", sort=True):
            counts = _counts_for_lines(group.drop(columns=["_wall_epoch", "_hour"]), line_dicts)
            dt_label = datetime.fromtimestamp(int(hour_epoch), tz=timezone.utc).strftime("%Y-%m-%d %H:00 UTC")
            buckets_out.append({
                "hour_start_epoch": int(hour_epoch),
                "hour_label": dt_label,
                "total_unique_tracks": counts["total_unique_tracks"],
                "per_line": counts["per_line"],
            })

    return {
        "buckets": buckets_out,
        "wall_clock_buckets": sync_map.get("wall_clock_buckets", []),
        "rows_used": len(filtered),
        "num_segments": sync_map.get("num_segments", 0),
    }


def compute_corrected_counts(
    project_id: str,
    video_id: str,
    line_ids: List[str],
) -> Dict:
    gap_data = load_gap_map(project_id, video_id)
    if gap_data is None:
        raise ValueError("timestamp gap map not available — run timestamp-scan first")

    lines = counter_client.list_lines(video_id)
    selected = [ln for ln in lines if ln["id"] in line_ids]
    if len(selected) != len(line_ids):
        raise ValueError("one or more line_ids do not belong to this video")

    line_dicts = [
        {"id": ln["id"], "name": ln["name"], "a": ln["points"]["a"], "b": ln["points"]["b"]}
        for ln in selected
    ]

    tracks = _load_tracks(project_id, video_id)
    filtered = _filter_gap_frames(tracks, gap_data["gaps"])
    raw_counts = _counts_for_lines(tracks, line_dicts)
    corrected = _counts_for_lines(filtered, line_dicts)

    rows_before = len(tracks)
    rows_after = len(filtered)
    return {
        "gap_stats": gap_data["stats"],
        "rows_excluded": rows_before - rows_after,
        "rows_excluded_fraction": round((rows_before - rows_after) / max(rows_before, 1), 4),
        "raw_counts": raw_counts,
        "corrected_counts": corrected,
        "delta_per_line": _delta(raw_counts, corrected),
    }


def _delta(raw: Dict, corrected: Dict) -> List[Dict]:
    raw_by_id = {p["line_id"]: p for p in raw.get("per_line", [])}
    deltas = []
    for cp in corrected.get("per_line", []):
        rp = raw_by_id.get(cp["line_id"], {})
        deltas.append({
            "line_id": cp["line_id"],
            "line_name": cp["line_name"],
            "raw_total": rp.get("total", 0),
            "corrected_total": cp["total"],
            "excluded": rp.get("total", 0) - cp["total"],
        })
    return deltas