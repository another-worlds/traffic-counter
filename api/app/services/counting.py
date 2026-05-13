"""
Counting math.

A track is a polyline P = [(x_0,y_0), ..., (x_{n-1},y_{n-1})] indexed by sampled frames.
A counting line is the segment L = (A, B).

A track *crosses* L if any of its consecutive segments (P_k, P_{k+1}) intersects L.

Segment-segment intersection — robust 2D test using cross-product signs:
    d1 = sign( (B - A) x (P - A) )   # which side of L is P on
    d2 = sign( (B - A) x (Q - A) )   # which side of L is Q on
    d3 = sign( (Q - P) x (A - P) )   # which side of PQ is A on
    d4 = sign( (Q - P) x (B - P) )   # which side of PQ is B on
The segments intersect strictly iff d1 != d2 AND d3 != d4.
(Collinear/touching cases are rare in track data and we treat them as non-crossings.)

Direction of crossing at segment k:
    sign( (B - A) x (Q - P) )
gives +1 if the track is moving from L's "left" to L's "right" (in image coords,
this corresponds to one consistent flow direction along the road).
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple


# COCO class ids we keep from a vehicle-counting detector (Ultralytics defaults).
COCO_VEHICLE_CLASSES = {
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}


def _cross_2d(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """2D cross product u x v, broadcast over leading axes."""
    return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]


def count_crossings_for_line(
    tracks_df: pd.DataFrame,
    line_a: Tuple[float, float],
    line_b: Tuple[float, float],
) -> Dict:
    """
    Count unique tracks that cross the line A->B.

    tracks_df must have columns: track_id, frame_idx, class_id, cx, cy
    Tracks are grouped by track_id; segments are formed between consecutive frames
    for the same track.

    Each crossing track is counted exactly once per line. Direction is determined
    at the first crossing segment.

    Returns:
        {
            "total": int,
            "track_ids": list[int],
            "by_class": {class_name: count},
            "by_direction": {"positive": int, "negative": int},
        }
    """
    A = np.asarray(line_a, dtype=np.float64)
    B = np.asarray(line_b, dtype=np.float64)
    AB = B - A

    by_class: Dict[str, int] = {}
    by_dir = {"positive": 0, "negative": 0}
    crossing_ids: List[int] = []

    if tracks_df.empty:
        return {"total": 0, "track_ids": [], "by_class": by_class, "by_direction": by_dir}

    # Sort once for groupby.
    df = tracks_df.sort_values(["track_id", "frame_idx"], kind="mergesort")

    for track_id, g in df.groupby("track_id", sort=False):
        pts = g[["cx", "cy"]].to_numpy(dtype=np.float64, copy=False)
        if pts.shape[0] < 2:
            continue
        P = pts[:-1]
        Q = pts[1:]
        PQ = Q - P

        # Signs from the four cross products.
        d1 = np.sign(_cross_2d(AB, P - A))
        d2 = np.sign(_cross_2d(AB, Q - A))
        d3 = np.sign(_cross_2d(PQ, A - P))
        d4 = np.sign(_cross_2d(PQ, B - P))

        intersect = (d1 != d2) & (d3 != d4)
        if not intersect.any():
            continue

        # First crossing segment
        k = int(np.argmax(intersect))

        # Class of the track — use the modal class across the track's detections
        # (occasional misclassifications shouldn't flip the bucket).
        cls_id = int(g["class_id"].mode().iloc[0])
        cls_name = COCO_VEHICLE_CLASSES.get(cls_id, f"class_{cls_id}")
        by_class[cls_name] = by_class.get(cls_name, 0) + 1

        # Direction at the crossing
        d = _cross_2d(AB, PQ[k])
        if d >= 0:
            by_dir["positive"] += 1
        else:
            by_dir["negative"] += 1

        crossing_ids.append(int(track_id))

    return {
        "total": len(crossing_ids),
        "track_ids": crossing_ids,
        "by_class": by_class,
        "by_direction": by_dir,
    }


def total_unique_tracks(tracks_df: pd.DataFrame) -> int:
    if tracks_df.empty:
        return 0
    return int(tracks_df["track_id"].nunique())


def compute_counts_for_lines(
    tracks_df: pd.DataFrame,
    lines: List[Dict],  # each: {"id": str, "name": str, "a": [x,y], "b": [x,y]}
) -> Dict:
    """
    Compute counts for every line over a tracks dataframe (already merged across
    all selected videos), and return the per-line result with both percentage views.
    """
    T = total_unique_tracks(tracks_df)
    per_line_raw = []
    for ln in lines:
        r = count_crossings_for_line(tracks_df, tuple(ln["a"]), tuple(ln["b"]))
        r["line_id"] = ln["id"]
        r["line_name"] = ln["name"]
        per_line_raw.append(r)

    sum_across = sum(r["total"] for r in per_line_raw)

    per_line = []
    for r in per_line_raw:
        pct_video = (100.0 * r["total"] / T) if T > 0 else 0.0
        pct_drawn = (100.0 * r["total"] / sum_across) if sum_across > 0 else 0.0
        per_line.append({
            "line_id": r["line_id"],
            "line_name": r["line_name"],
            "total": r["total"],
            "by_class": r["by_class"],
            "by_direction": r["by_direction"],
            "percent_of_video_total": round(pct_video, 2),
            "percent_of_drawn_lines": round(pct_drawn, 2),
        })

    return {
        "total_unique_tracks": T,
        "sum_across_lines": sum_across,
        "per_line": per_line,
    }
