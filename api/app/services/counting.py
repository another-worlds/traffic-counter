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

Performance: counting a line over a long video used to re-sort the entire
tracks DataFrame and rebuild per-track numpy arrays on every call (once per
line). For a 4-hour CCTV recording with ~1M rows and ~10 lines that turned
into 30-90s of compute. The hot path now consumes a MaterializedTracks
struct (sorted + grouped + modal-class precomputed once per video, cached
in services.tracks alongside the parquet); the per-line work drops to a
handful of vectorised numpy ops per track.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Dict, List, NamedTuple, Tuple, Union


# COCO class ids we keep from a vehicle-counting detector (Ultralytics defaults).
COCO_VEHICLE_CLASSES = {
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}


class MaterializedTracks(NamedTuple):
    """Per-video tracks pre-shaped for line-crossing tests.

    Built once per video (sort + groupby + modal-class), then reused across
    every counting line for that video. Build is amortised by the parquet
    cache in services.tracks.
    """
    track_ids: np.ndarray         # (T,) int64
    pts: List[np.ndarray]         # T entries, each (S_i, 2) float64 contiguous
    modal_class: np.ndarray       # (T,) int16, argmax(bincount(class_id)) per track


_EMPTY_MATERIALIZED = MaterializedTracks(
    track_ids=np.empty(0, dtype=np.int64),
    pts=[],
    modal_class=np.empty(0, dtype=np.int16),
)


def _cross_2d(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """2D cross product u x v, broadcast over leading axes."""
    return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]


def materialize_tracks(tracks_df: pd.DataFrame) -> MaterializedTracks:
    """Sort once, group once, compute per-track modal class once.

    Drops single-point "tracks" since they cannot cross any line. The output
    is what every counting-line call wants — no DataFrame work inside the
    hot loop.
    """
    if tracks_df is None or tracks_df.empty:
        return _EMPTY_MATERIALIZED
    df = tracks_df.sort_values(["track_id", "frame_idx"], kind="mergesort")
    tids: List[int] = []
    pts_list: List[np.ndarray] = []
    modals: List[int] = []
    for tid, g in df.groupby("track_id", sort=False):
        pts = g[["cx", "cy"]].to_numpy(dtype=np.float64, copy=True)
        if pts.shape[0] < 2:
            continue
        cls = g["class_id"].to_numpy()
        # bincount.argmax is ~10× faster than pd.Series.mode for small int arrays
        # and avoids a per-call DataFrame allocation.
        modal = int(np.bincount(cls.astype(np.int64, copy=False)).argmax())
        tids.append(int(tid))
        pts_list.append(pts)
        modals.append(modal)
    return MaterializedTracks(
        track_ids=np.asarray(tids, dtype=np.int64),
        pts=pts_list,
        modal_class=np.asarray(modals, dtype=np.int16),
    )


def materialized_nbytes(mt: MaterializedTracks) -> int:
    """Approximate in-RAM byte size for the cache budget accounting."""
    return (
        int(mt.track_ids.nbytes)
        + int(mt.modal_class.nbytes)
        + sum(int(a.nbytes) for a in mt.pts)
    )


TracksLike = Union[pd.DataFrame, MaterializedTracks]


def _as_materialized(tracks: TracksLike) -> MaterializedTracks:
    return tracks if isinstance(tracks, MaterializedTracks) else materialize_tracks(tracks)


def count_crossings_for_line(
    tracks: TracksLike,
    line_a: Tuple[float, float],
    line_b: Tuple[float, float],
) -> Dict:
    """
    Count unique tracks that cross the line A->B.

    `tracks` may be a raw DataFrame (it will be materialised on the fly — fine
    for one-off calls) or, far more efficiently, a `MaterializedTracks` built
    once and reused across many lines.

    Each crossing track is counted exactly once per line. Direction is
    determined at the first crossing segment.
    """
    mt = _as_materialized(tracks)

    A = np.asarray(line_a, dtype=np.float64)
    B = np.asarray(line_b, dtype=np.float64)
    AB = B - A

    by_class: Dict[str, int] = {}
    by_dir = {"positive": 0, "negative": 0}
    by_class_dir: Dict[str, Dict[str, int]] = {"positive": {}, "negative": {}}
    crossing_ids: List[int] = []

    if mt.track_ids.size == 0:
        return {
            "total": 0, "track_ids": [],
            "by_class": by_class, "by_direction": by_dir,
            "by_class_direction": by_class_dir,
        }

    for i, pts in enumerate(mt.pts):
        P = pts[:-1]
        Q = pts[1:]
        PQ = Q - P

        d1 = np.sign(_cross_2d(AB, P - A))
        d2 = np.sign(_cross_2d(AB, Q - A))
        d3 = np.sign(_cross_2d(PQ, A - P))
        d4 = np.sign(_cross_2d(PQ, B - P))

        intersect = (d1 != d2) & (d3 != d4)
        if not intersect.any():
            continue

        k = int(np.argmax(intersect))
        cls_id = int(mt.modal_class[i])
        cls_name = COCO_VEHICLE_CLASSES.get(cls_id, f"class_{cls_id}")
        direction = "positive" if _cross_2d(AB, PQ[k]) >= 0 else "negative"

        by_class[cls_name] = by_class.get(cls_name, 0) + 1
        by_dir[direction] += 1
        by_class_dir[direction][cls_name] = by_class_dir[direction].get(cls_name, 0) + 1

        crossing_ids.append(int(mt.track_ids[i]))

    return {
        "total": len(crossing_ids),
        "track_ids": crossing_ids,
        "by_class": by_class,
        "by_direction": by_dir,
        "by_class_direction": by_class_dir,
    }


def total_unique_tracks(tracks: TracksLike) -> int:
    if isinstance(tracks, MaterializedTracks):
        return int(tracks.track_ids.size)
    if tracks is None or tracks.empty:
        return 0
    return int(tracks["track_id"].nunique())


def compute_counts_for_lines(
    tracks: TracksLike,
    lines: List[Dict],  # each: {"id": str, "name": str, "a": [x,y], "b": [x,y]}
) -> Dict:
    """
    Compute counts for every line. Materialises the tracks once and reuses
    that shape across every line in `lines` — callers in hot paths should
    pass a pre-built MaterializedTracks (see `services.tracks.load_materialized_tracks`).
    """
    mt = _as_materialized(tracks)
    T = int(mt.track_ids.size)

    per_line_raw = []
    for ln in lines:
        r = count_crossings_for_line(mt, tuple(ln["a"]), tuple(ln["b"]))
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
            "by_class_direction": r["by_class_direction"],
            "percent_of_video_total": round(pct_video, 2),
            "percent_of_drawn_lines": round(pct_drawn, 2),
        })

    return {
        "total_unique_tracks": T,
        "sum_across_lines": sum_across,
        "per_line": per_line,
    }
