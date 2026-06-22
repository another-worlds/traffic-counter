"""Network sources: OSMnx import (online), a bundled sample grid (offline), and a
simple GeoJSON parser. All return plain (nodes, links) dicts with fresh UUID ids,
ready to persist as ORM rows. Links are directed (a two-way street = two links).
"""
from __future__ import annotations

import math
import uuid
from typing import Dict, List, Tuple

import numpy as np


def _uuid() -> str:
    return str(uuid.uuid4())


# --------------------------------------------------------------------------- #
# Bundled sample grid (offline demo / tests)                                  #
# --------------------------------------------------------------------------- #
def build_sample_network(
    rows: int = 4,
    cols: int = 4,
    lat0: float = 41.30,
    lon0: float = 69.26,
    spacing_m: float = 300.0,
    free_flow_speed_ms: float = 13.9,
    lanes: int = 1,
) -> Tuple[List[dict], List[dict], Dict[Tuple[int, int], str]]:
    dlat = spacing_m / 111_111.0
    dlon = spacing_m / (111_111.0 * math.cos(math.radians(lat0)))

    grid: Dict[Tuple[int, int], str] = {}
    nodes: List[dict] = []
    for r in range(rows):
        for c in range(cols):
            nid = _uuid()
            grid[(r, c)] = nid
            nodes.append({
                "id": nid, "name": f"n_{r}_{c}",
                "geom": {"type": "Point", "coordinates": [lon0 + c * dlon, lat0 + r * dlat]},
                "osm_id": None,
            })

    by_id = {n["id"]: n for n in nodes}
    links: List[dict] = []
    # Deterministic ±15% speed jitter so shortest paths are unique — without it a
    # regular grid has many tied paths and UXsim's route choice spreads flow in a way
    # the static shortest-path ODME incidence can't track (calibration oscillates).
    srng = np.random.default_rng(12345)

    def add_link(a: str, b: str) -> None:
        ax, ay = by_id[a]["geom"]["coordinates"]
        bx, by = by_id[b]["geom"]["coordinates"]
        links.append({
            "id": _uuid(), "name": f"{by_id[a]['name']}->{by_id[b]['name']}",
            "from_node_id": a, "to_node_id": b,
            "geom": {"type": "LineString", "coordinates": [[ax, ay], [bx, by]]},
            "length_m": spacing_m, "lanes": lanes,
            "free_flow_speed_ms": free_flow_speed_ms * float(srng.uniform(0.85, 1.15)),
            "jam_density": 0.2, "oneway": False,
        })

    for r in range(rows):
        for c in range(cols):
            if c + 1 < cols:
                add_link(grid[(r, c)], grid[(r, c + 1)])
                add_link(grid[(r, c + 1)], grid[(r, c)])
            if r + 1 < rows:
                add_link(grid[(r, c)], grid[(r + 1, c)])
                add_link(grid[(r + 1, c)], grid[(r, c)])

    return nodes, links, grid


# --------------------------------------------------------------------------- #
# OSMnx import (online)                                                        #
# --------------------------------------------------------------------------- #
def _parse_speed(maxspeed) -> float:
    if isinstance(maxspeed, list):
        maxspeed = maxspeed[0] if maxspeed else None
    if maxspeed is None:
        return 13.9  # ~50 km/h
    try:
        kph = float(str(maxspeed).split()[0])
        if "mph" in str(maxspeed).lower():
            kph *= 1.60934
        return max(kph / 3.6, 2.0)
    except Exception:
        return 13.9


def _parse_lanes(lanes) -> int:
    if isinstance(lanes, list):
        lanes = lanes[0] if lanes else None
    try:
        return max(int(float(lanes)), 1)
    except Exception:
        return 1


def import_bbox(south: float, west: float, north: float, east: float) -> Tuple[List[dict], List[dict]]:
    import osmnx as ox  # heavy + online; imported lazily

    try:  # osmnx >= 2.0 takes bbox=(left, bottom, right, top)
        G = ox.graph_from_bbox(bbox=(west, south, east, north), network_type="drive")
    except TypeError:  # osmnx 1.x positional (north, south, east, west)
        G = ox.graph_from_bbox(north, south, east, west, network_type="drive")

    nodes: List[dict] = []
    idmap: Dict[object, str] = {}
    for osmid, data in G.nodes(data=True):
        nid = _uuid()
        idmap[osmid] = nid
        nodes.append({
            "id": nid, "name": str(osmid),
            "geom": {"type": "Point", "coordinates": [float(data["x"]), float(data["y"])]},
            "osm_id": str(osmid),
        })

    links: List[dict] = []
    for u, v, data in G.edges(data=True):
        if u not in idmap or v not in idmap:
            continue
        geom = data.get("geometry")
        if geom is not None:
            coords = [[float(x), float(y)] for x, y in geom.coords]
        else:
            coords = [[float(G.nodes[u]["x"]), float(G.nodes[u]["y"])],
                      [float(G.nodes[v]["x"]), float(G.nodes[v]["y"])]]
        links.append({
            "id": _uuid(), "name": f"{u}-{v}",
            "from_node_id": idmap[u], "to_node_id": idmap[v],
            "geom": {"type": "LineString", "coordinates": coords},
            "length_m": float(data.get("length", 0.0) or 50.0),
            "lanes": _parse_lanes(data.get("lanes")),
            "free_flow_speed_ms": _parse_speed(data.get("maxspeed")),
            "jam_density": 0.2, "oneway": bool(data.get("oneway", False)),
        })

    return nodes, links


# --------------------------------------------------------------------------- #
# GeoJSON upload                                                              #
# --------------------------------------------------------------------------- #
def parse_geojson(fc: dict) -> Tuple[List[dict], List[dict]]:
    """Minimal parser: Point features with properties.kind=='node' (and an 'id'),
    LineString features with properties.kind=='link' referencing 'from'/'to' node ids."""
    nodes: List[dict] = []
    links: List[dict] = []
    idmap: Dict[str, str] = {}
    for feat in fc.get("features", []):
        props = feat.get("properties", {}) or {}
        geom = feat.get("geometry", {}) or {}
        if props.get("kind") == "node" and geom.get("type") == "Point":
            nid = _uuid()
            idmap[str(props.get("id"))] = nid
            nodes.append({"id": nid, "name": str(props.get("name", props.get("id"))),
                          "geom": geom, "osm_id": None})
    for feat in fc.get("features", []):
        props = feat.get("properties", {}) or {}
        geom = feat.get("geometry", {}) or {}
        if props.get("kind") == "link" and geom.get("type") == "LineString":
            a, b = str(props.get("from")), str(props.get("to"))
            if a not in idmap or b not in idmap:
                continue
            links.append({"id": _uuid(), "name": str(props.get("name", f"{a}-{b}")),
                          "from_node_id": idmap[a], "to_node_id": idmap[b], "geom": geom,
                          "length_m": float(props.get("length_m", 0.0) or 0.0),
                          "lanes": int(props.get("lanes", 1)),
                          "free_flow_speed_ms": float(props.get("free_flow_speed_ms", 13.9)),
                          "jam_density": 0.2, "oneway": bool(props.get("oneway", False))})
    return nodes, links
