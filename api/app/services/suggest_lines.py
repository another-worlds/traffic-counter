"""
Suggest counting lines by scoring candidate segments against track coverage.

Algorithm:
  1. Generate ~360 candidate line segments: 18 angles × 20 perpendicular offsets.
  2. Score each candidate using count_crossings_for_line (unique tracks crossed).
  3. Greedily select top-N non-overlapping candidates (suppress spatially similar lines).
"""
from __future__ import annotations
import math
from typing import List, Dict

import numpy as np
import pandas as pd

from .counting import count_crossings_for_line


def _generate_candidates(w: int, h: int, n_angles: int = 18, n_offsets: int = 20) -> List[Dict]:
    """Return candidate line segments covering the frame at various angles and positions."""
    cx, cy = w / 2.0, h / 2.0
    diag = math.sqrt(w * w + h * h)
    candidates: List[Dict] = []

    for i in range(n_angles):
        theta = math.radians(i * 180.0 / n_angles)  # 0 … 170°
        dx, dy = math.cos(theta), math.sin(theta)
        nx, ny = -dy, dx  # perpendicular (normal)

        for j in range(n_offsets):
            frac = j / (n_offsets - 1) if n_offsets > 1 else 0.5
            offset = (frac - 0.5) * min(w, h) * 0.85
            px = cx + offset * nx
            py = cy + offset * ny
            half = diag * 0.6
            a = [round(px - half * dx, 1), round(py - half * dy, 1)]
            b = [round(px + half * dx, 1), round(py + half * dy, 1)]
            candidates.append({
                "a": a,
                "b": b,
                "midpoint": (px, py),
                "theta": theta,
            })

    return candidates


def _too_similar(line1: Dict, line2: Dict, w: int, h: int) -> bool:
    """True if two candidates are spatially close AND similarly angled (suppress duplicates)."""
    mx1, my1 = line1["midpoint"]
    mx2, my2 = line2["midpoint"]
    dist = math.sqrt((mx1 - mx2) ** 2 + (my1 - my2) ** 2)
    min_dist = min(w, h) * 0.08

    angle_diff = abs(line1["theta"] - line2["theta"]) % math.pi
    angle_diff = min(angle_diff, math.pi - angle_diff)

    return dist < min_dist and angle_diff < math.radians(20)


def suggest_lines(
    tracks_df: pd.DataFrame,
    w: int,
    h: int,
    n_suggestions: int = 3,
) -> List[Dict]:
    """
    Return up to *n_suggestions* non-overlapping counting-line suggestions,
    each as ``{"points": {"a": [...], "b": [...]}, "coverage_count": int, "coverage_percent": float}``.
    """
    if tracks_df.empty:
        return []

    total_tracks = int(tracks_df["track_id"].nunique())
    candidates = _generate_candidates(w, h)

    # Score every candidate
    for c in candidates:
        result = count_crossings_for_line(tracks_df, c["a"], c["b"])
        c["score"] = result["total"]

    candidates.sort(key=lambda c: c["score"], reverse=True)

    # Greedy selection with spatial suppression
    selected: List[Dict] = []
    for c in candidates:
        if c["score"] == 0:
            break
        if not any(_too_similar(c, s, w, h) for s in selected):
            selected.append(c)
            if len(selected) >= n_suggestions:
                break

    return [
        {
            "points": {"a": c["a"], "b": c["b"]},
            "coverage_count": c["score"],
            "coverage_percent": round(100.0 * c["score"] / total_tracks, 1) if total_tracks else 0.0,
        }
        for c in selected
    ]
