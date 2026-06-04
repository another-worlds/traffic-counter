"""Build ipyleaflet *elements* from the macromodel-api GeoJSON payloads.

Solara requires widgets to be created at render time (not import time) and dynamic
layers to be passed declaratively to ``Map.element(layers=[...])`` — an element has no
``add_layer``. So every builder returns a list of element objects created via the
``.element(...)`` constructor Solara adds to each widget class.
"""
from __future__ import annotations

import ipyleaflet as L

GREEN, AMBER, RED = "#2ca25f", "#f4a300", "#d7301f"
GRAY, BLUE, COUNTER = "#8a8a8a", "#3182bd", "#e24b4a"

OSM_TILES = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"


def color_for_geh(g):
    if g is None:
        return GRAY
    if g < 5:
        return GREEN
    if g < 10:
        return AMBER
    return RED


def tile_layer_element():
    return L.TileLayer.element(url=OSM_TILES, attribution="© OpenStreetMap", base=True)


def network_elements(fc: dict) -> list:
    links = {"type": "FeatureCollection",
             "features": [f for f in fc["features"] if f["properties"].get("kind") == "link"]}
    return [L.GeoJSON.element(data=links, style={"color": GRAY, "weight": 2, "opacity": 0.55})]


def flows_elements(fc: dict) -> list:
    feats = [f for f in fc["features"] if f.get("geometry", {}).get("type") == "LineString"]
    data = {"type": "FeatureCollection", "features": feats}

    def style_cb(feature):
        p = feature["properties"]
        sim = p.get("sim_vph") or 0.0
        weight = 2 + min(sim / 150.0, 9)
        g = p.get("geh")
        if g is not None:
            return {"color": color_for_geh(g), "weight": max(weight, 4), "opacity": 0.95}
        return {"color": "#4575b4", "weight": weight, "opacity": 0.5}

    return [L.GeoJSON.element(data=data, style_callback=style_cb)]


def zone_elements(fc: dict) -> list:
    out = []
    for f in fc.get("features", []):
        geom = f.get("geometry")
        if not geom:
            continue
        lon, lat = geom["coordinates"]
        out.append(L.CircleMarker.element(
            location=(lat, lon), radius=9, color=BLUE, fill_color=BLUE, fill_opacity=0.45, weight=1))
    return out


def counter_elements(fc: dict) -> list:
    out = []
    for f in fc.get("features", []):
        lon, lat = f["geometry"]["coordinates"]
        out.append(L.CircleMarker.element(
            location=(lat, lon), radius=6, color=COUNTER, fill_color=COUNTER, fill_opacity=0.9, weight=2))
    return out


def bounds_of(fc: dict):
    lats, lons = [], []
    for f in fc.get("features", []):
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates")
        if geom.get("type") == "Point":
            lons.append(coords[0]); lats.append(coords[1])
        elif geom.get("type") == "LineString":
            for x, y in coords:
                lons.append(x); lats.append(y)
    if not lats:
        return None
    return [[min(lats), min(lons)], [max(lats), max(lons)]]
