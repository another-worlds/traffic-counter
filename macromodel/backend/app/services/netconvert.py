"""Convert a stored network (nodes/links as GeoJSON dicts) into:
  * a UXsim ``World`` (the macroscopic simulation engine), and
  * a networkx ``DiGraph`` (for travel-time skims and the ODME path/link incidence).

Geographic coordinates (lat/lon) are projected to a local UTM zone so node x,y and
link lengths are metric. ``uxsim`` is imported lazily so the pure-numeric services
(gravity, GEH) can be used/tested without it installed.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import networkx as nx
from pyproj import Transformer


def _utm_epsg(lat: float, lon: float) -> int:
    zone = int((lon + 180.0) / 6.0) + 1
    return (32600 if lat >= 0 else 32700) + zone


def make_projector(nodes: List[dict]) -> Transformer:
    coords = [n["geom"]["coordinates"] for n in nodes]
    lon0 = sum(c[0] for c in coords) / len(coords)
    lat0 = sum(c[1] for c in coords) / len(coords)
    epsg = _utm_epsg(lat0, lon0)
    return Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)


def project_xy(nodes: List[dict]) -> Dict[str, Tuple[float, float]]:
    t = make_projector(nodes)
    out: Dict[str, Tuple[float, float]] = {}
    for n in nodes:
        lon, lat = n["geom"]["coordinates"]
        x, y = t.transform(lon, lat)
        out[n["id"]] = (float(x), float(y))
    return out


def build_graph(nodes: List[dict], links: List[dict]) -> nx.DiGraph:
    """Directed graph; edge weight = free-flow travel time (s), carries link_id."""
    G = nx.DiGraph()
    for n in nodes:
        G.add_node(n["id"])
    for l in links:
        speed = max(float(l.get("free_flow_speed_ms") or 13.9), 1.0)
        length = max(float(l.get("length_m") or 1.0), 1.0)
        G.add_edge(
            l["from_node_id"], l["to_node_id"],
            link_id=l["id"], weight=length / speed, length=length,
        )
    return G


def build_world(nodes: List[dict], links: List[dict], deltan: int = 5, tmax: int = 3600):
    """Build a UXsim World. Returns (World, xy projection)."""
    from uxsim import World  # lazy: keeps numeric tests import-light

    xy = project_xy(nodes)
    W = World(
        name="macromodel", deltan=deltan, tmax=tmax,
        print_mode=0, save_mode=0, show_mode=0, random_seed=0,
    )
    for n in nodes:
        x, y = xy[n["id"]]
        W.addNode(str(n["id"]), x, y)
    for l in links:
        W.addLink(
            str(l["id"]), str(l["from_node_id"]), str(l["to_node_id"]),
            length=max(float(l.get("length_m") or 1.0), 10.0),
            free_flow_speed=max(float(l.get("free_flow_speed_ms") or 13.9), 1.0),
            jam_density=float(l.get("jam_density") or 0.2),
            number_of_lanes=int(l.get("lanes") or 1),
        )
    return W, xy
