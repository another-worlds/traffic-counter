"""The georeferencing bridge: traffic-counter counts -> directional link volumes.

A counting line lives in pixel space with image-relative direction (positive/negative).
We snap the counter to the nearest directed link and convert the chosen direction's
total to an hourly volume, plus a PCU (car-equivalent) volume the single-class model
calibrates against.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from shapely.geometry import Point, shape

# Passenger Car Units per vehicle class (traffic-counter COCO classes).
PCU = {"bicycle": 0.3, "car": 1.0, "motorcycle": 0.5, "bus": 2.5, "truck": 2.0}


def snap_to_link(lon: float, lat: float, links: List[dict]) -> Optional[dict]:
    """Nearest directed link to a point (degree-space distance; fine at street scale)."""
    pt = Point(lon, lat)
    best = None
    best_d = float("inf")
    for l in links:
        try:
            geom = shape(l["geom"])
        except Exception:
            continue
        d = geom.distance(pt)
        if d < best_d:
            best_d = d
            best = l
    return best


def pcu_factor(by_class: Dict[str, float]) -> float:
    total = sum(by_class.values())
    if total <= 0:
        return 1.0
    return sum(PCU.get(k, 1.0) * v for k, v in by_class.items()) / total


def direction_key(link_direction: str) -> str:
    """Map a link direction (AB/BA) to a counting-line direction (positive/negative)."""
    return "positive" if (link_direction or "AB").upper() == "AB" else "negative"


def counts_to_targets(counts_resp: dict, line_id: str, link_direction: str, hours: float) -> Optional[dict]:
    """Turn a traffic-counter /counts response into vph / pcu_vph for one line+direction."""
    per_line = {p.get("line_id"): p for p in counts_resp.get("per_line", [])}
    entry = per_line.get(line_id)
    if not entry:
        return None
    by_class = entry.get("by_class", {}) or {}
    by_dir = entry.get("by_direction", {}) or {}
    key = direction_key(link_direction)
    directional_total = by_dir.get(key)
    if directional_total is None:
        directional_total = entry.get("total", 0)
    hours = max(float(hours), 1e-6)
    veh_vph = float(directional_total) / hours
    return {
        "observed_vph": veh_vph,
        "pcu_vph": veh_vph * pcu_factor(by_class),
        "by_class": by_class,
        "by_direction": by_dir,
    }
