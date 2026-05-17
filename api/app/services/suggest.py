"""
Automatic counting-line placement via trajectory-density analysis.

Algorithm:
  1. Divide the video frame into a GRID_N × GRID_N spatial grid.
  2. Assign each track's centroid to a grid cell and count tracks per cell.
  3. Sort cells by occupancy (descending) and select the top N distinct clusters
     (separated by at least MIN_SEP_FRAC of the frame diagonal).
  4. For each selected cell:
     a. Collect all tracks whose centroid falls in the cell.
     b. Compute the mean velocity direction from (first_point → last_point) of each
        track (only tracks with ≥2 points). The perpendicular to the mean velocity is
        the natural counting line orientation.
     c. Place a line of length LINE_LEN_FRAC × min(W, H) centred on the cell centroid,
        oriented perpendicular to mean traffic flow. Clamp to image bounds.
  5. Score each candidate line by running count_crossings_for_line on the full tracks
     dataframe (fast – pure numpy).
  6. Return up to N lines sorted by score (highest first), with auto-generated names
     and colors from a fixed palette.
"""
from __future__ import annotations

import math
from typing import List, Dict

import numpy as np
import pandas as pd

from .counting import count_crossings_for_line

# ──────────────────────────── tuneable constants ──────────────────────────── #
GRID_N = 5              # grid resolution per axis
LINE_LEN_FRAC = 0.45   # candidate line length as fraction of min(W, H)
MIN_SEP_FRAC = 0.20    # minimum centre-to-centre separation (fraction of diagonal)

_PALETTE = [
    "#4ecdc4",  # teal
    "#f7b731",  # amber
    "#a29bfe",  # lavender
    "#fd79a8",  # pink
    "#00b894",  # green
]
# ────────────────────────────────────────────────────────────────────────────── #


def _track_centroid_and_direction(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Return a DataFrame with one row per track containing:
      cx_mean, cy_mean, dx, dy  (direction from first to last detection)
    """
    rows = []
    for tid, g in df.sort_values("frame_idx").groupby("track_id", sort=False):
        pts = g[["cx", "cy"]].to_numpy(dtype=np.float64)
        cx_mean = pts[:, 0].mean()
        cy_mean = pts[:, 1].mean()
        if pts.shape[0] >= 2:
            dx = float(pts[-1, 0] - pts[0, 0])
            dy = float(pts[-1, 1] - pts[0, 1])
        else:
            dx, dy = 0.0, 0.0
        rows.append({"track_id": tid, "cx": cx_mean, "cy": cy_mean, "dx": dx, "dy": dy})
    if not rows:
        return pd.DataFrame(columns=["track_id", "cx", "cy", "dx", "dy"])
    return pd.DataFrame(rows)


def _perpendicular(dx: float, dy: float) -> tuple[float, float]:
    """Return a unit vector perpendicular to (dx, dy)."""
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 1.0, 0.0
    # Perpendicular to (dx, dy) is (-dy, dx)
    return -dy / length, dx / length


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _make_line_from_cell(
    cx_mean: float,
    cy_mean: float,
    mean_dx: float,
    mean_dy: float,
    line_half_len: float,
    w: int,
    h: int,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Create a line segment centred at (cx_mean, cy_mean) perpendicular to traffic flow."""
    px, py = _perpendicular(mean_dx, mean_dy)
    ax = _clamp(cx_mean - px * line_half_len, 0.0, float(w))
    ay = _clamp(cy_mean - py * line_half_len, 0.0, float(h))
    bx = _clamp(cx_mean + px * line_half_len, 0.0, float(w))
    by = _clamp(cy_mean + py * line_half_len, 0.0, float(h))
    return (ax, ay), (bx, by)


def suggest_lines(
    tracks_df: pd.DataFrame,
    video_width: int,
    video_height: int,
    n: int = 3,
) -> List[Dict]:
    """
    Suggest up to *n* counting lines that cover the densest trajectory clusters.

    Returns a list of dicts with keys: name, points ({"a": [x,y], "b": [x,y]}),
    color, score (number of tracks the line would cross).
    """
    if tracks_df.empty or n <= 0:
        return []

    W, H = float(video_width), float(video_height)
    diag = math.hypot(W, H)
    min_sep_sq = (MIN_SEP_FRAC * diag) ** 2
    line_half_len = LINE_LEN_FRAC * min(W, H) / 2.0

    centroids = _track_centroid_and_direction(tracks_df)
    if centroids.empty:
        return []

    # ── 1. Assign tracks to grid cells ── #
    cell_w = W / GRID_N
    cell_h = H / GRID_N
    centroids["col"] = (centroids["cx"] / cell_w).clip(0, GRID_N - 1).astype(int)
    centroids["row"] = (centroids["cy"] / cell_h).clip(0, GRID_N - 1).astype(int)
    centroids["cell"] = centroids["row"] * GRID_N + centroids["col"]

    # ── 2. Sort cells by track count ── #
    cell_counts = centroids.groupby("cell").size().sort_values(ascending=False)

    # ── 3. Select top-N non-redundant cluster centres ── #
    selected: list[tuple[float, float, float, float]] = []  # (cx, cy, mean_dx, mean_dy)

    for cell_id in cell_counts.index:
        cell_tracks = centroids[centroids["cell"] == cell_id]
        cx_ctr = float(cell_tracks["cx"].mean())
        cy_ctr = float(cell_tracks["cy"].mean())

        # Separation check vs. already-selected centres
        too_close = False
        for (sx, sy, _, _) in selected:
            if (cx_ctr - sx) ** 2 + (cy_ctr - sy) ** 2 < min_sep_sq:
                too_close = True
                break
        if too_close:
            continue

        # Mean flow direction for tracks in this cell
        vecs = cell_tracks[["dx", "dy"]].to_numpy(dtype=np.float64)
        moving = vecs[np.linalg.norm(vecs, axis=1) > 1e-6]
        if moving.shape[0] > 0:
            # Normalise each direction vector so slow and fast tracks vote equally
            norms = np.linalg.norm(moving, axis=1, keepdims=True)
            unit = moving / norms
            mean_dx, mean_dy = float(unit[:, 0].mean()), float(unit[:, 1].mean())
        else:
            mean_dx, mean_dy = 0.0, 0.0

        selected.append((cx_ctr, cy_ctr, mean_dx, mean_dy))
        if len(selected) >= n:
            break

    # ── 4. Build and score candidate lines ── #
    results = []
    for idx, (cx_ctr, cy_ctr, mean_dx, mean_dy) in enumerate(selected):
        pt_a, pt_b = _make_line_from_cell(cx_ctr, cy_ctr, mean_dx, mean_dy,
                                          line_half_len, video_width, video_height)
        crossing = count_crossings_for_line(tracks_df, pt_a, pt_b)
        score = crossing["total"]
        color = _PALETTE[idx % len(_PALETTE)]
        results.append({
            "name": f"suggested {idx + 1}",
            "points": {"a": list(pt_a), "b": list(pt_b)},
            "color": color,
            "score": score,
        })

    # ── 5. Sort by score descending ── #
    results.sort(key=lambda r: r["score"], reverse=True)
    return results
