"""Load timestamp artifacts from shared storage."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from ..storage import (
    get_storage,
    key_timestamp_gaps,
    key_timestamp_index,
    key_timestamp_region,
    key_timestamp_region_preview,
    key_timestamp_status,
    key_timestamp_timeline,
    key_timestamp_sync_map,
)
from ..db import load_scan, load_scans_for_videos, upsert_scan
from .timeline_viz import build_timeline_visualization


def resolve_project_id(video_id: str, hint: Optional[str] = None) -> Optional[str]:
    if hint:
        return hint
    storage = get_storage()
    index_key = key_timestamp_index(video_id)
    if storage.exists(index_key):
        doc = storage.read_json(index_key)
        return doc.get("project_id")
    return None


def write_video_index(
    project_id: str,
    video_id: str,
    filename: str,
    local_source_path: Optional[str] = None,
) -> None:
    storage = get_storage()
    storage.write_json(key_timestamp_index(video_id), {
        "project_id": project_id,
        "filename": filename,
        "local_source_path": local_source_path,
    })


def read_video_index(video_id: str) -> Optional[Dict[str, Any]]:
    storage = get_storage()
    index_key = key_timestamp_index(video_id)
    if not storage.exists(index_key):
        return None
    return storage.read_json(index_key)


def _load_region_doc(project_id: str, video_id: str) -> Optional[Dict[str, Any]]:
    storage = get_storage()
    region_key = key_timestamp_region(project_id, video_id)
    if not storage.exists(region_key):
        return None
    return storage.read_json(region_key)


def _enrich_status_doc(project_id: str, video_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    data = dict(data)
    data["artifacts"] = _artifact_flags(project_id, video_id)
    if data["artifacts"].get("region") and not data.get("region"):
        region = _load_region_doc(project_id, video_id)
        if region:
            data["region"] = region
    return data


def get_statuses_for_project(
    project_id: str,
    video_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Bulk status lookup for dashboard polling."""
    if not video_ids:
        return {}
    db_rows = load_scans_for_videos(video_ids)
    storage = get_storage()
    out: Dict[str, Dict[str, Any]] = {}
    for vid in video_ids:
        row = db_rows.get(vid)
        if row is None:
            row = _backfill_db_from_storage(project_id, vid)
        if row:
            out[vid] = _enrich_status_doc(project_id, vid, row)
            continue
        status_key = key_timestamp_status(project_id, vid)
        if storage.exists(status_key):
            data = storage.read_json(status_key)
            out[vid] = _enrich_status_doc(project_id, vid, data)
        else:
            out[vid] = _enrich_status_doc(project_id, vid, {"status": "pending"})
    return out


def get_status(project_id: str, video_id: str) -> Dict[str, Any]:
    db_row = load_scan(video_id)
    if db_row is None:
        db_row = _backfill_db_from_storage(project_id, video_id)
    if db_row:
        return _enrich_status_doc(project_id, video_id, db_row)
    storage = get_storage()
    status_key = key_timestamp_status(project_id, video_id)
    if not storage.exists(status_key):
        return _enrich_status_doc(project_id, video_id, {"status": "pending"})
    data = storage.read_json(status_key)
    return _enrich_status_doc(project_id, video_id, data)


def persist_scan(video_id: str, payload: Dict[str, Any]) -> None:
    """Write scan state to Postgres (when configured)."""
    upsert_scan(video_id, payload)


def _load_gap_map_from_storage(project_id: str, video_id: str) -> Optional[Dict[str, Any]]:
    storage = get_storage()
    gaps_key = key_timestamp_gaps(project_id, video_id)
    if not storage.exists(gaps_key):
        return None
    gaps = storage.read_json(gaps_key)
    region = None
    region_key = key_timestamp_region(project_id, video_id)
    if storage.exists(region_key):
        region = storage.read_json(region_key)
    timeline_summary = None
    timeline_df = None
    timeline_key = key_timestamp_timeline(project_id, video_id)
    if storage.exists(timeline_key):
        with storage.open_read(timeline_key) as fp:
            timeline_df = pd.read_parquet(fp)
        timeline_summary = {
            "num_samples": len(timeline_df),
            "present_fraction": round(float(timeline_df["present"].mean()), 4) if len(timeline_df) else 0.0,
            "first_t_s": float(timeline_df["t_seconds"].iloc[0]) if len(timeline_df) else None,
            "last_t_s": float(timeline_df["t_seconds"].iloc[-1]) if len(timeline_df) else None,
        }
    gap_list = gaps.get("gaps", [])
    gap_stats = gaps.get("stats", {})
    sync_map = None
    sync_map_key = key_timestamp_sync_map(project_id, video_id)
    if storage.exists(sync_map_key):
        sync_map = storage.read_json(sync_map_key)
    hour_presence = gaps.get("hour_presence") or (sync_map or {}).get("hour_presence")
    hour_coherence = gaps.get("hour_coherence") or (sync_map or {}).get("hour_coherence")
    ideal_day = gaps.get("ideal_day") or (sync_map or {}).get("ideal_day")
    timeline_viz = build_timeline_visualization(
        gap_list, gap_stats, timeline_df,
        hour_presence=hour_presence,
        hour_coherence=hour_coherence,
        ideal_day=ideal_day,
    )
    return {
        "region": region,
        "gaps": gap_list,
        "stats": gap_stats,
        "timeline_summary": timeline_summary,
        "timeline_viz": timeline_viz,
        "wall_clock_buckets": gaps.get("wall_clock_buckets") or (sync_map or {}).get("wall_clock_buckets"),
        "hour_presence": hour_presence,
        "hour_coherence": hour_coherence,
        "ideal_day": ideal_day,
        "sync_map": sync_map,
        "num_segments": gaps.get("num_segments") or (sync_map or {}).get("num_segments"),
    }


def _backfill_db_from_storage(project_id: str, video_id: str) -> Optional[Dict[str, Any]]:
    """One-shot migration: copy completed storage artifacts into Postgres."""
    data = _load_gap_map_from_storage(project_id, video_id)
    if data is None:
        return None
    storage = get_storage()
    status_key = key_timestamp_status(project_id, video_id)
    status_doc = storage.read_json(status_key) if storage.exists(status_key) else {}
    payload = {
        "status": status_doc.get("status", "done"),
        "progress": status_doc.get("progress"),
        "region": data.get("region"),
        "gaps": data.get("gaps"),
        "stats": data.get("stats"),
        "timeline_summary": data.get("timeline_summary"),
        "timeline_viz": data.get("timeline_viz"),
        "error_message": status_doc.get("error_message"),
        "started_at": status_doc.get("started_at"),
        "completed_at": status_doc.get("completed_at"),
    }
    persist_scan(video_id, payload)
    return payload


def _artifact_flags(project_id: str, video_id: str) -> Dict[str, bool]:
    storage = get_storage()
    return {
        "region": storage.exists(key_timestamp_region(project_id, video_id)),
        "region_preview": storage.exists(key_timestamp_region_preview(project_id, video_id)),
        "timeline": storage.exists(key_timestamp_timeline(project_id, video_id)),
        "sync_map": storage.exists(key_timestamp_sync_map(project_id, video_id)),
        "gaps": storage.exists(key_timestamp_gaps(project_id, video_id)),
    }


def _merge_presence_fields(result: Dict[str, Any], fresh: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Prefer hour_presence / ideal_day from storage when DB snapshot is stale."""
    if not fresh:
        return result
    if not result.get("hour_presence") and fresh.get("hour_presence"):
        result["hour_presence"] = fresh["hour_presence"]
    if not result.get("hour_coherence") and fresh.get("hour_coherence"):
        result["hour_coherence"] = fresh["hour_coherence"]
    if not result.get("ideal_day") and fresh.get("ideal_day"):
        result["ideal_day"] = fresh["ideal_day"]
    stats = dict(result.get("stats") or {})
    fresh_stats = fresh.get("stats") or {}
    if (
        fresh_stats.get("hour_presence_map_enabled") or fresh_stats.get("coherence_map_enabled")
    ) and not (stats.get("hour_presence_map_enabled") or stats.get("coherence_map_enabled")):
        stats.update({
            k: fresh_stats[k]
            for k in (
                "hour_presence_map_enabled", "coherence_map_enabled",
                "parsed_fraction", "parsed_bins", "coherent_fraction", "coherent_bins",
                "hours_with_coverage", "hour_presence", "hour_coherence", "ideal_day",
            )
            if k in fresh_stats
        })
        result["stats"] = stats
    if result.get("timeline_viz"):
        viz = dict(result["timeline_viz"])
        if result.get("hour_presence"):
            viz["hour_presence"] = result["hour_presence"]
        if result.get("hour_coherence"):
            viz["hour_coherence"] = result["hour_coherence"]
        viz["ideal_day"] = result.get("ideal_day")
        viz["hour_presence_map_enabled"] = bool(
            stats.get("hour_presence_map_enabled") or stats.get("coherence_map_enabled")
        )
        viz["coherence_map_enabled"] = viz["hour_presence_map_enabled"]
        result["timeline_viz"] = viz
    return result


def load_gap_map(project_id: str, video_id: str) -> Optional[Dict[str, Any]]:
    fresh = _load_gap_map_from_storage(project_id, video_id)
    db_row = load_scan(video_id)
    if db_row is None:
        db_row = _backfill_db_from_storage(project_id, video_id)
    if db_row and db_row.get("status") == "done" and db_row.get("gaps") is not None:
        gaps_list = db_row.get("gaps") or []
        if isinstance(gaps_list, dict):
            gaps_list = gaps_list.get("gaps", [])
        stats = db_row.get("stats") or {}
        return _merge_presence_fields({
            "region": db_row.get("region"),
            "gaps": gaps_list,
            "stats": stats,
            "timeline_summary": db_row.get("timeline_summary"),
            "timeline_viz": db_row.get("timeline_viz"),
            "wall_clock_buckets": stats.get("wall_clock_buckets"),
            "hour_presence": stats.get("hour_presence") or (fresh or {}).get("hour_presence"),
            "hour_coherence": stats.get("hour_coherence") or (fresh or {}).get("hour_coherence"),
            "ideal_day": stats.get("ideal_day") or (fresh or {}).get("ideal_day"),
            "num_segments": stats.get("num_segments"),
            "sync_map": (fresh or {}).get("sync_map"),
        }, fresh)

    return fresh


def gap_frame_mask(gaps: List[Dict], total_frames: int) -> List[bool]:
    """Dense boolean mask: True if frame is inside a gap."""
    mask = [False] * total_frames
    for g in gaps:
        start = max(0, int(g["start_frame"]))
        end = min(total_frames, int(g["end_frame"]))
        for i in range(start, end):
            mask[i] = True
    return mask