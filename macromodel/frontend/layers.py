"""Build ipyleaflet layers from the macromodel-api GeoJSON payloads."""
from __future__ import annotations

import ipyleaflet as L
import ipywidgets as W

GREEN, AMBER, RED = "#2ca25f", "#f4a300", "#d7301f"
GRAY, BLUE, COUNTER = "#8a8a8a", "#3182bd", "#e24b4a"


def color_for_geh(g):
    if g is None:
        return GRAY
    if g < 5:
        return GREEN
    if g < 10:
        return AMBER
    return RED


def network_layer(fc: dict) -> L.GeoJSON:
    links = {"type": "FeatureCollection",
             "features": [f for f in fc["features"] if f["properties"].get("kind") == "link"]}
    return L.GeoJSON(data=links, style={"color": GRAY, "weight": 2, "opacity": 0.55}, name="network")


def linkflow_layer(fc: dict) -> L.GeoJSON:
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

    return L.GeoJSON(data=data, style_callback=style_cb, name="flows")


def zone_layer(fc: dict) -> L.LayerGroup:
    markers = []
    for f in fc.get("features", []):
        geom = f.get("geometry")
        if not geom:
            continue
        lon, lat = geom["coordinates"]
        p = f["properties"]
        markers.append(L.CircleMarker(
            location=(lat, lon), radius=9, color=BLUE, fill_color=BLUE, fill_opacity=0.45, weight=1,
            popup=W.HTML(f"<b>{p.get('name')}</b><br>P={p.get('production'):.0f} A={p.get('attraction'):.0f}"),
        ))
    return L.LayerGroup(layers=markers, name="zones")


def counter_layer(fc: dict) -> L.LayerGroup:
    markers = []
    for f in fc.get("features", []):
        lon, lat = f["geometry"]["coordinates"]
        p = f["properties"]
        obs = p.get("observed_vph")
        label = f"<b>{p.get('name')}</b><br>obs={obs:.0f} vph" if obs else f"<b>{p.get('name')}</b><br>(no obs)"
        markers.append(L.CircleMarker(
            location=(lat, lon), radius=6, color=COUNTER, fill_color=COUNTER, fill_opacity=0.9, weight=2,
            popup=W.HTML(label),
        ))
    return L.LayerGroup(layers=markers, name="counters")


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
