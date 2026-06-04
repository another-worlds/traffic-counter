"""ipyleaflet LIVE-widget builders for the Network editor.

The Network section keeps a single stable Map widget (created once via use_memo) and
swaps its ``.layers`` imperatively, so these builders return live widgets (not Solara
elements). Creating widgets here is safe: they're built inside a use_effect (kernel
context), never at import time.
"""
from __future__ import annotations

import ipyleaflet as L

import theme

GREEN, AMBER, RED = "#2ca25f", "#f4a300", "#d7301f"
GRAY, BLUE, COUNTER = "#8a8a8a", "#3aa0ff", "#ff5470"
NODE, STOP, LINE, CONN = "#8aa0c2", "#b07cff", "#37c8c3", "#5b6b86"
DESIRE = "#ffd166"
VOL_BINS = [(300, "#c6dbef"), (700, "#6baed6"), (1200, "#2171b5"), (1e9, "#08306b")]


def color_for_geh(g):
    if g is None:
        return GRAY
    return GREEN if g < 5 else (AMBER if g < 10 else RED)


def color_for_vc(vc):
    if vc is None:
        return GRAY
    return GREEN if vc < 0.6 else (AMBER if vc < 0.9 else RED)


def color_for_volume(v):
    for hi, c in VOL_BINS:
        if v < hi:
            return c
    return VOL_BINS[-1][1]


def dark_tile():
    return L.TileLayer(url=theme.DARK_TILES, attribution="© OpenStreetMap, © CARTO", base=True)


# --- network geometry (from /map) ----------------------------------------- #
def _circles(fc, color, radius, fill=0.9):
    out = []
    for f in fc.get("features", []):
        g = f.get("geometry")
        if not g or g.get("type") != "Point":
            continue
        lon, lat = g["coordinates"]
        out.append(L.CircleMarker(location=(lat, lon), radius=radius, color=color,
                                  fill_color=color, fill_opacity=fill, weight=1, stroke=True))
    return out


def node_widgets(fc):
    return _circles(fc, NODE, 4, 0.9)


def zone_widgets(fc):
    return _circles(fc, BLUE, 9, 0.4)


def stop_widgets(fc):
    return _circles(fc, STOP, 5)


def detector_widgets(fc):
    return _circles(fc, COUNTER, 6)


def plain_link_widgets(fc):
    return [L.GeoJSON(data=fc, style={"color": GRAY, "weight": 2, "opacity": 0.6})]


def connector_widgets(fc):
    return [L.GeoJSON(data=fc, style={"color": CONN, "weight": 1.5, "opacity": 0.7, "dashArray": "4,4"})]


def line_widgets(fc):
    return [L.GeoJSON(data=fc, style={"color": LINE, "weight": 4, "opacity": 0.85})]


def flow_link_widgets(fc, color_by):
    """Links coloured by GEH / Volume / V·C, width ∝ volume."""
    def style_cb(feature):
        p = feature["properties"]
        sim = p.get("sim_vph")
        if sim is None:
            return {"color": GRAY, "weight": 2, "opacity": 0.6}
        weight = 2 + min(sim / 150.0, 9)
        if color_by == "Volume":
            return {"color": color_for_volume(sim), "weight": max(weight, 3), "opacity": 0.95}
        if color_by == "V/C":
            return {"color": color_for_vc(p.get("vc")), "weight": max(weight, 4), "opacity": 0.95}
        g = p.get("geh")
        if g is not None:
            return {"color": color_for_geh(g), "weight": max(weight, 4), "opacity": 0.95}
        return {"color": "#4575b4", "weight": weight, "opacity": 0.6}

    return [L.GeoJSON(data=fc, style_callback=style_cb)]


def desire_widgets(zones_fc, labels, values, topn=40):
    """OD desire lines between zone centroids, width ∝ matrix volume."""
    cent = {}
    for f in zones_fc.get("features", []):
        g = f.get("geometry")
        if g and g.get("type") == "Point":
            lon, lat = g["coordinates"]
            cent[f["properties"].get("name")] = (lat, lon)
    trips = []
    n = len(labels)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            v = float(values[i][j])
            if v > 0 and labels[i] in cent and labels[j] in cent:
                trips.append((v, labels[i], labels[j]))
    trips.sort(reverse=True)
    trips = trips[:topn]
    vmax = trips[0][0] if trips else 1.0
    out = []
    for v, a, b in trips:
        out.append(L.Polyline(locations=[cent[a], cent[b]], color=DESIRE,
                              weight=1 + 7 * (v / vmax), opacity=0.5, fill=False))
    return out


def selected_ring(lon, lat):
    return L.CircleMarker(location=(lat, lon), radius=13, color="#ff2d2d",
                          fill_color="#ff2d2d", fill_opacity=0.22, weight=3)


def legend_html(color_by, has_flows):
    if not has_flows:
        return "<div class='mm-legend'><b>Links</b><br>Run Procedures to colour by result.</div>"
    if color_by == "Volume":
        rows = "".join(f"<div><i style='background:{c}'></i>&lt; {hi:g} vph</div>"
                       for hi, c in VOL_BINS[:-1]) + \
            f"<div><i style='background:{VOL_BINS[-1][1]}'></i>≥ {VOL_BINS[-2][0]:g}</div>"
        title = "Volume"
    elif color_by == "V/C":
        rows = (f"<div><i style='background:{GREEN}'></i>&lt; 0.6</div>"
                f"<div><i style='background:{AMBER}'></i>0.6–0.9</div>"
                f"<div><i style='background:{RED}'></i>&gt; 0.9 (over capacity)</div>")
        title = "V/C ratio"
    else:
        rows = (f"<div><i style='background:{GREEN}'></i>GEH &lt; 5</div>"
                f"<div><i style='background:{AMBER}'></i>5–10</div>"
                f"<div><i style='background:{RED}'></i>&gt; 10</div>")
        title = "GEH (fit)"
    return f"<div class='mm-legend'><b>{title}</b>{rows}</div>"


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
