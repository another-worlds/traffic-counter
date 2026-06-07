"""Build downsampled timeline visualization payloads for the UI."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from worker.gap_detector import normalize_by_reason, primary_gap_reason

GAP_KINDS = frozenset({
    "missing_osd", "time_jump", "time_reverse", "frozen_osd", "sync_recovery",
})


def _gap_kind(reason: str) -> str:
    primary = primary_gap_reason(reason)
    if primary in GAP_KINDS:
        return primary
    return "gap"


def build_coverage_segments(
    gaps: List[Dict[str, Any]],
    total_duration_s: float,
) -> List[Dict[str, Any]]:
    """Merge gap intervals with valid spans for the timeline bar."""
    if total_duration_s <= 0:
        return []
    ordered = sorted(gaps, key=lambda g: float(g.get("start_t_s", 0)))
    segments: List[Dict[str, Any]] = []
    cursor = 0.0
    for g in ordered:
        start = max(0.0, float(g.get("start_t_s", 0)))
        end = min(total_duration_s, float(g.get("end_t_s", 0)))
        if end <= start:
            continue
        if start > cursor + 0.01:
            segments.append({
                "start_t_s": round(cursor, 2),
                "end_t_s": round(start, 2),
                "kind": "valid",
            })
        segments.append({
            "start_t_s": round(start, 2),
            "end_t_s": round(end, 2),
            "kind": _gap_kind(str(g.get("reason", "gap"))),
            "reason": g.get("reason"),
        })
        cursor = max(cursor, end)
    if cursor < total_duration_s - 0.01:
        segments.append({
            "start_t_s": round(cursor, 2),
            "end_t_s": round(total_duration_s, 2),
            "kind": "valid",
        })
    if not segments and total_duration_s > 0:
        segments.append({
            "start_t_s": 0.0,
            "end_t_s": round(total_duration_s, 2),
            "kind": "valid",
        })
    return segments


def _frame_in_gap(frame_idx: int, gaps: List[Dict[str, Any]]) -> Optional[str]:
    for g in gaps:
        if int(g.get("start_frame", -1)) <= frame_idx < int(g.get("end_frame", -1)):
            return _gap_kind(str(g.get("reason", "gap")))
    return None


def build_presence_track(
    df: pd.DataFrame,
    gaps: List[Dict[str, Any]],
    max_points: int = 180,
) -> List[Dict[str, Any]]:
    """Downsampled OSD presence + gap membership for the coherence strip."""
    if df.empty:
        return []
    work = df
    if len(work) > max_points:
        step = max(1, len(work) // max_points)
        work = work.iloc[::step]
    points: List[Dict[str, Any]] = []
    for row in work.itertuples(index=False):
        frame_idx = int(row.frame_idx)
        gap_kind = _frame_in_gap(frame_idx, gaps)
        points.append({
            "t_s": round(float(row.t_seconds), 2),
            "present": bool(row.present),
            "in_gap": gap_kind is not None,
            "gap_kind": gap_kind,
        })
    return points


def build_timeline_visualization(
    gaps: List[Dict[str, Any]],
    stats: Dict[str, Any],
    timeline_df: Optional[pd.DataFrame],
    max_points: int = 180,
) -> Dict[str, Any]:
    total_duration_s = float(stats.get("total_duration_s") or 0)
    total_frames = int(stats.get("total_frames") or 0)
    by_reason = normalize_by_reason(stats.get("by_reason") or {})
    segments = build_coverage_segments(gaps, total_duration_s)
    presence = (
        build_presence_track(timeline_df, gaps, max_points=max_points)
        if timeline_df is not None and not timeline_df.empty
        else []
    )
    valid_duration_s = max(0.0, total_duration_s - float(stats.get("gap_duration_s") or 0))
    return {
        "total_duration_s": total_duration_s,
        "total_frames": total_frames,
        "valid_duration_s": round(valid_duration_s, 2),
        "gap_duration_s": float(stats.get("gap_duration_s") or 0),
        "segments": segments,
        "presence": presence,
        "gap_breakdown": by_reason,
        "num_gaps": int(stats.get("num_gaps") or len(gaps)),
    }