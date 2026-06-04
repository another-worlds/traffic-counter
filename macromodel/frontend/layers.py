"""ipyleaflet LIVE-widget builders for the Network editor.

The Network section keeps a single stable Map widget (created once via use_memo) and
swaps its ``.layers`` imperatively, so these builders return live widgets (not Solara
elements). Creating widgets here is safe: they're built inside a use_effect (kernel
context), never at import time.
"""
from __future__ import annotations

import math

import ipyleaflet as L
import ipywidgets as W

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


def aerial_tile():
    t = L.basemap_to_tiles(L.basemaps.Esri.WorldImagery)
    t.base = True
    return t


def osm_tile():
    t = L.basemap_to_tiles(L.basemaps.OpenStreetMap.Mapnik)
    t.base = True
    return t


def base_tile(name):
    """Base tile layer for the Display basemap selector (aerial = trace from photos)."""
    if name == "Aerial":
        return aerial_tile()
    if name == "OSM":
        return osm_tile()
    return dark_tile()


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
    out = []
    for f in fc.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") == "Polygon":
            out.append(L.GeoJSON(data=f, style={"color": BLUE, "weight": 1.5, "opacity": 0.7,
                                                "fillColor": BLUE, "fillOpacity": 0.16}))
            c = (f.get("properties") or {}).get("centroid")
            if c:
                out.append(L.CircleMarker(location=(c[1], c[0]), radius=5, color=BLUE,
                                          fill_color=BLUE, fill_opacity=0.8, weight=1))
        elif g.get("type") == "Point":
            lon, lat = g["coordinates"]
            out.append(L.CircleMarker(location=(lat, lon), radius=9, color=BLUE,
                                      fill_color=BLUE, fill_opacity=0.4, weight=1))
    return out


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


def hover_ring(lon, lat):
    return L.CircleMarker(location=(lat, lon), radius=10, color="#7CFC8A",
                          fill_color="#7CFC8A", fill_opacity=0.12, weight=2)


def _label_point(geom, props):
    t = geom.get("type")
    if t == "Point":
        return geom["coordinates"]
    if t == "LineString":
        cs = geom["coordinates"]
        return cs[len(cs) // 2]
    if t == "Polygon":
        c = props.get("centroid")
        if c:
            return c
        ring = geom["coordinates"][0]
        return [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]
    return props.get("centroid")


def label_widgets(fc, field="name", color="#cfe0ff"):
    """Name labels as tiny DivIcon markers (toggleable; capped by the caller)."""
    out = []
    for f in fc.get("features", []):
        p = f.get("properties") or {}
        txt = p.get(field)
        pt = _label_point(f.get("geometry") or {}, p)
        if not txt or pt is None:
            continue
        lon, lat = pt
        html = (f"<div style='font-size:11px;color:{color};white-space:nowrap;"
                f"text-shadow:0 0 3px #000,0 0 3px #000;pointer-events:none'>{txt}</div>")
        out.append(L.Marker(location=(lat, lon), draggable=False, keyboard=False,
                            icon=L.DivIcon(html=html, icon_size=[1, 1], icon_anchor=[-5, 7])))
    return out


# --- detector line→link assignment (Visum-style highlight + direction arrow) ------ #
def assign_highlight(coords, to_end):
    """Highlight the candidate directed link in red with an arrowhead at its to-end,
    so the user sees which link + travel direction a grabbed counting line will bind to."""
    locs = [(lat, lon) for lon, lat in coords]
    out = [L.Polyline(locations=locs, color="#ff2d2d", weight=6, opacity=0.9, fill=False)]
    arrow = _arrow_marker(coords, to_end)
    if arrow is not None:
        out.append(arrow)
    return out


def _arrow_marker(coords, to_end):
    """A rotated ➤ DivIcon sitting on the link near its to-end, pointing along travel."""
    if len(coords) < 2:
        return None
    if list(coords[-1]) == list(to_end):
        a, b = coords[-2], coords[-1]
    else:
        a, b = coords[1], coords[0]
    deg = math.degrees(math.atan2(-(b[1] - a[1]), (b[0] - a[0])))  # screen angle: 0°=east, CW+
    px, py = a[0] * 0.3 + b[0] * 0.7, a[1] * 0.3 + b[1] * 0.7      # 70% toward the to-end
    html = (f"<div style='font-size:22px;color:#ff2d2d;line-height:1;"
            f"transform:rotate({deg:.0f}deg);transform-origin:center'>&#10148;</div>")
    icon = L.DivIcon(html=html, icon_size=[24, 24], icon_anchor=[12, 12])
    return L.Marker(location=(py, px), icon=icon, draggable=False, keyboard=False)


# --- detector inspection popup (camera frame + counts + analysis status) ---------- #
def _seg_dot(status):
    c = {"done": "#2ca25f", "analyzing": "#f4a300", "error": "#d7301f"}.get(status, "#8a8a8a")
    return (f"<span style='display:inline-block;width:9px;height:9px;border-radius:2px;"
            f"background:{c};margin:0 1px'></span>")


def _detector_stats_html(info):
    v = info.get("video") or {}
    status = v.get("status") or "?"
    parts = ["<div style='font-size:12px;color:#dbe6f7;line-height:1.5'>"]
    parts.append(f"<b style='color:#e8eef9'>{info.get('name') or 'detector'}</b><br>")
    if status == "analyzed":
        parts.append(f"<span style='color:#2ca25f'>● analyzed</span> · "
                     f"{int(v.get('num_tracks') or 0)} tracks<br>")
    else:
        prog = v.get("progress_pct")
        pct = f" {int(prog * 100)}%" if isinstance(prog, (int, float)) else ""
        parts.append(f"<span style='color:#f4a300'>● {status}{pct}</span><br>")

    obs, pcu = info.get("observed_vph"), info.get("pcu_vph")
    if obs is not None:
        parts.append(f"<b style='color:#9fb3d4'>Observed</b> {obs:.0f} vph · "
                     f"<b style='color:#9fb3d4'>PCU</b> {(pcu or 0):.0f} ({info.get('link_direction') or 'AB'})<br>")

    counts, ts = info.get("counts"), info.get("track_stats")
    if counts:
        bd = counts.get("by_direction") or {}
        parts.append(f"<b style='color:#9fb3d4'>{counts.get('line_name') or 'line'}</b> "
                     f"{int(counts.get('total') or 0)} "
                     f"(＋{int(bd.get('positive', 0))} / －{int(bd.get('negative', 0))})<br>")
        bc = counts.get("by_class") or {}
        if bc:
            parts.append("<span style='color:#8aa0c2'>"
                         + ", ".join(f"{k}:{int(n)}" for k, n in bc.items()) + "</span><br>")
    elif ts:
        parts.append(f"<b style='color:#9fb3d4'>Tracks</b> {int(ts.get('total_tracks') or 0)}<br>")
        bc = ts.get("by_class") or {}
        if bc:
            parts.append("<span style='color:#8aa0c2'>"
                         + ", ".join(f"{k}:{int(n)}" for k, n in bc.items()) + "</span><br>")

    segs = info.get("segments") or []
    if segs:
        done = sum(1 for s in segs if s.get("status") == "done")
        parts.append(f"<b style='color:#9fb3d4'>Segments</b> {done}/{len(segs)} "
                     + "".join(_seg_dot(s.get("status")) for s in segs) + "<br>")

    url = info.get("open_video_url")
    if url:
        parts.append(f"<a href='{url}' target='_blank' style='display:inline-block;margin-top:7px;"
                     f"padding:5px 12px;background:#3aa0ff;color:#06101f;border-radius:6px;"
                     f"text-decoration:none;font-weight:700'>▶ Open video</a>")
    parts.append("</div>")
    return "".join(parts)


def _img_html(url):
    if not url:
        return ("<div style='height:130px;display:flex;align-items:center;justify-content:center;"
                "color:#8aa0c2;background:#0c0f14;border-radius:6px'>no frame yet</div>")
    return (f"<img src='{url}' style='width:100%;max-height:200px;object-fit:cover;"
            f"border-radius:6px' onerror=\"this.style.display='none'\"/>")


def detector_popup(info, lat, lon, on_flip=None):
    """A map popup showing the detector's camera frame (keyframe/trajectories/heatmap toggle),
    counting stats, per-segment + overall analysis status, and an Open-video button."""
    if not info or not info.get("reachable", False):
        msg = (info or {}).get("error") or "counter unavailable"
        child = W.HTML(value=f"<div style='padding:6px;color:#dbe6f7'>Detector source "
                             f"unavailable<br><small style='color:#8aa0c2'>{msg}</small></div>")
        return L.Popup(location=(lat, lon), child=child, max_width=320,
                       auto_close=False, close_on_escape_key=True)

    imgs = info.get("images") or {}
    opts = [(lab, k) for lab, k in
            (("Keyframe", "keyframe"), ("Trajectories", "trajectories"), ("Heatmap", "heatmap"))
            if imgs.get(k)]
    children = []
    img = W.HTML(value=_img_html(imgs.get(opts[0][1]) if opts else None))
    if len(opts) > 1:
        tb = W.ToggleButtons(options=opts, value=opts[0][1])
        tb.observe(lambda ch: setattr(img, "value", _img_html(imgs.get(ch["new"]))), "value")
        children.append(tb)
    children.append(img)
    children.append(W.HTML(value=_detector_stats_html(info)))
    if on_flip is not None:
        btn = W.Button(description="Flip count direction", icon="exchange",
                       layout=W.Layout(width="auto", margin="4px 0 0 0"))
        btn.on_click(lambda _b: on_flip())
        children.append(btn)
    child = W.VBox(children, layout=W.Layout(width="300px"))
    return L.Popup(location=(lat, lon), child=child, max_width=340, min_width=300,
                   auto_close=False, close_on_escape_key=True)


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
        t = geom.get("type")
        if t == "Point":
            lons.append(coords[0]); lats.append(coords[1])
        elif t == "LineString":
            for x, y in coords:
                lons.append(x); lats.append(y)
        elif t == "Polygon":
            for x, y in coords[0]:
                lons.append(x); lats.append(y)
    if not lats:
        return None
    return [[min(lats), min(lons)], [max(lats), max(lons)]]
