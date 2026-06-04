"""Network section — a Visum-style editor.

Stable ipyleaflet widget (use_memo); layers swapped imperatively. Selection mode: LMB
drag draws a rubber-band box that multi-selects (bulk edit/delete); single click selects
one; right-click inspects. Creation mode: LMB inserts the active element; the Zone tool
places a centroid then draws a boundary polygon (click vertices, click the first to close).
A Display panel adds thematic colouring, per-layer toggles, desire lines, and an attribute
filter that hides non-matching elements.
"""
from __future__ import annotations

import ipyleaflet as L
import ipywidgets as W
import solara

import actions
import api_client as api
import layers
import state
import theme
from sections import lists

TOOLS = ["Select", "Node", "Link", "Zone", "Connector", "Stop", "Detector"]

_desire_cache = solara.reactive(None)
_qv_buf = solara.reactive({})
_qv_id = solara.reactive("")
_bulk_type = solara.reactive("")
_bulk_attr = solara.reactive("")
_bulk_val = solara.reactive("")

_MAP = {"m": None}                                   # the stable map widget
_rubber = {"start": None, "rect": None}              # rubber-band box (Selection drag)
_zone = {"centroid": None, "verts": [], "line": None}  # zone polygon being drawn
_assign = {"link": None, "hl": []}                   # detector line→link drag: candidate + highlight layers
_draft = {"line": None, "from_ring": None, "snap": None}  # live link-drawing preview layers
_hover = {"layer": None, "key": None}                # hover-highlight under the cursor (Selection)

FILTER_ATTRS = {"links": ["sim_vph", "vc", "lanes", "link_type_id"],
                "nodes": ["name"],
                "zones": ["population", "workplaces", "production", "attraction"]}


# --- geometry helpers ----------------------------------------------------- #
def _point_of(f):
    g = f.get("geometry") or {}
    if g.get("type") == "Point":
        return g["coordinates"]
    c = (f.get("properties") or {}).get("centroid")
    if c:
        return c
    if g.get("type") == "LineString":
        cs = g["coordinates"]
        return [(cs[0][0] + cs[-1][0]) / 2, (cs[0][1] + cs[-1][1]) / 2]
    if g.get("type") == "Polygon":
        ring = g["coordinates"][0]
        return [sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)]
    return None


def _nearest_node(lat, lon):
    md = state.map_data.value or {}
    best, bd = None, 1e18
    for f in md.get("nodes", {}).get("features", []):
        x, y = f["geometry"]["coordinates"]
        d = (x - lon) ** 2 + (y - lat) ** 2
        if d < bd:
            bd, best = d, f["properties"]["id"]
    return best


def _snap_tol(px=12):
    """Pixel snap tolerance as a degree distance at the map's current zoom."""
    m = _MAP["m"]
    z = getattr(m, "zoom", None) or state.map_zoom.value or 14
    return px * 360.0 / (256.0 * (2 ** z))


def _node_within(lat, lon, tol):
    """Nearest node id within `tol` degrees, else None (snap-or-create test)."""
    md = state.map_data.value or {}
    best, bd = None, tol * tol
    for f in md.get("nodes", {}).get("features", []):
        x, y = f["geometry"]["coordinates"]
        d = (x - lon) ** 2 + (y - lat) ** 2
        if d < bd:
            bd, best = d, f["properties"]["id"]
    return best


def _node_latlon(nid):
    md = state.map_data.value or {}
    for f in md.get("nodes", {}).get("features", []):
        if f["properties"]["id"] == nid:
            x, y = f["geometry"]["coordinates"]
            return (y, x)
    return None


def _nearest_any(lat, lon):
    md = state.map_data.value or {}
    best, bd = None, 1e18
    for obj in ("nodes", "zones", "detectors", "stops", "links"):
        for f in md.get(obj, {}).get("features", []):
            p = _point_of(f)
            if not p:
                continue
            d = (p[0] - lon) ** 2 + (p[1] - lat) ** 2
            if d < bd:
                bd, best = d, {"obj": obj, "id": f["properties"]["id"], "props": f["properties"]}
    return best


_PICK_PRIO = {"nodes": 0, "detectors": 0, "stops": 0, "links": 1, "zones": 2}


def _pick(lat, lon, tol=None):
    """Tolerance-gated selection under the cursor. Links use perpendicular distance;
    points (nodes/stops/detectors) are preferred over links over zones. None = empty."""
    md = state.map_data.value or {}
    tol = _snap_tol(14) if tol is None else tol
    tol2 = tol * tol
    best, bkey = None, (9, 1e18)
    for obj in ("nodes", "detectors", "stops", "links", "zones"):
        for f in md.get(obj, {}).get("features", []):
            g = f.get("geometry") or {}
            if obj == "links" and g.get("type") == "LineString" and len(g.get("coordinates", [])) >= 2:
                d = _link_d2(lon, lat, g["coordinates"])
            else:
                p = _point_of(f)
                if not p:
                    continue
                d = (p[0] - lon) ** 2 + (p[1] - lat) ** 2
            if d > tol2:
                continue
            key = (_PICK_PRIO[obj], d)
            if key < bkey:
                bkey, best = key, {"obj": obj, "id": f["properties"]["id"], "props": f["properties"]}
    return best


def _coords_of(sel):
    md = state.map_data.value or {}
    for f in md.get(sel["obj"], {}).get("features", []):
        if f["properties"].get("id") == sel["id"]:
            return _point_of(f)
    return None


def _select(sel):
    state.selected_many.value = []
    state.selected.value = sel
    state.drag_pos.value = _coords_of(sel) if (sel and sel["obj"] == "nodes") else None
    if sel:
        _qv_buf.value = {f: sel["props"].get(f) for f in lists.FIELDS.get(sel["obj"], [])}
        _qv_id.value = sel["id"]
    # Clicking a detector opens its camera/counts popup; anything else dismisses it.
    if sel and sel["obj"] == "detectors":
        co = _coords_of(sel)
        state.inspect_detector.value = {"id": sel["id"],
                                        "lat": co[1] if co else 0.0, "lon": co[0] if co else 0.0}
    else:
        state.inspect_detector.value = None
        state.detector_info.value = None


# --- detector ↔ traffic-counter embedding --------------------------------- #
def _load_counter_sources():
    try:
        state.counter_sources.value = api.counter_sources()
    except Exception as e:  # noqa: BLE001
        state.counter_sources.value = {"reachable": False, "error": str(e), "projects": []}


def _grab_line(video_id, line_id, name):
    state.grabbed_line.value = {"video_id": video_id, "line_id": line_id, "name": name}
    state.edit_mode.value = "Creation"
    state.active_tool.value = "Detector"
    state.status.value = f"Grabbed '{name}' — hover a link (red = chosen direction), click to drop."


def _cancel_grab():
    state.grabbed_line.value = None
    _clear_assign_highlight()
    state.status.value = "Assignment cancelled."


def _seg_point_d2(px, py, a, b):
    """Squared distance from point (px,py) to segment a-b in lon/lat space."""
    ax, ay = a; bx, by = b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return (px - cx) ** 2 + (py - cy) ** 2


def _link_d2(lon, lat, coords):
    return min(_seg_point_d2(lon, lat, coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _nearest_link_dir(lat, lon):
    """Nearest link, resolved to the *directed* row whose to-end the cursor points at."""
    md = state.map_data.value or {}
    feats = [f for f in md.get("links", {}).get("features", [])
             if (f.get("geometry") or {}).get("type") == "LineString"
             and len(f["geometry"]["coordinates"]) >= 2]
    if not feats:
        return None
    best = min(feats, key=lambda f: _link_d2(lon, lat, f["geometry"]["coordinates"]))
    cs = best["geometry"]["coordinates"]
    a, b = cs[0], cs[-1]
    da = (a[0] - lon) ** 2 + (a[1] - lat) ** 2
    db = (b[0] - lon) ** 2 + (b[1] - lat) ** 2
    want = tuple(b if db <= da else a)          # the end the cursor is nearer = travel target
    ends = {tuple(a), tuple(b)}
    chosen = best
    for f in feats:                              # prefer the directed row pointing at `want`
        c2 = f["geometry"]["coordinates"]
        if {tuple(c2[0]), tuple(c2[-1])} == ends and tuple(c2[-1]) == want:
            chosen = f
            break
    coords = chosen["geometry"]["coordinates"]
    return {"id": chosen["properties"]["id"], "coords": coords, "to_end": coords[-1]}


def _clear_assign_highlight():
    m = _MAP["m"]
    for h in _assign["hl"]:
        try:
            m.remove_layer(h)
        except Exception:  # noqa: BLE001
            pass
    _assign["hl"] = []
    _assign["link"] = None


def _update_assign_highlight(lat, lon):
    m = _MAP["m"]
    cand = _nearest_link_dir(lat, lon)
    _clear_assign_highlight()
    _assign["link"] = cand
    if cand and m is not None:
        hl = layers.assign_highlight(cand["coords"], cand["to_end"])
        for h in hl:
            m.add_layer(h)
        _assign["hl"] = hl


# --- live link-drawing preview (rubber-band + pending ring + snap dot) ----- #
def _clear_link_preview():
    m = _MAP["m"]
    for k in ("line", "from_ring", "snap"):
        if _draft[k] is not None and m is not None:
            try:
                m.remove_layer(_draft[k])
            except Exception:  # noqa: BLE001
                pass
        _draft[k] = None


def _update_link_preview(lat, lon):
    m = _MAP["m"]
    frm = _node_latlon(state.pending_link_from.value)
    if m is None or frm is None:
        return
    snap_id = _node_within(lat, lon, _snap_tol())
    end = _node_latlon(snap_id) if snap_id else (lat, lon)
    if _draft["line"] is None:
        _draft["line"] = L.Polyline(locations=[frm, end], color="#3aa0ff", weight=2,
                                    dash_array="6,6", fill=False)
        m.add_layer(_draft["line"])
    else:
        _draft["line"].locations = [frm, end]
    if _draft["from_ring"] is None:                 # highlight the chosen FROM node once
        _draft["from_ring"] = layers.selected_ring(frm[1], frm[0])
        m.add_layer(_draft["from_ring"])
    if snap_id:                                     # green dot = will attach to this node
        if _draft["snap"] is None:
            _draft["snap"] = L.CircleMarker(location=end, radius=8, color="#7CFC8A",
                                            fill_color="#7CFC8A", fill_opacity=0.4, weight=2)
            m.add_layer(_draft["snap"])
        else:
            _draft["snap"].location = end
    elif _draft["snap"] is not None:
        try:
            m.remove_layer(_draft["snap"])
        except Exception:  # noqa: BLE001
            pass
        _draft["snap"] = None


def _cancel_pending():
    """Abort an in-progress link/connector chain (Esc / Cancel button)."""
    state.pending_link_from.value = None
    _clear_link_preview()
    state.status.value = "Drawing stopped."


# --- hover highlight (Selection mode) ------------------------------------- #
def _clear_hover():
    m = _MAP["m"]
    if _hover["layer"] is not None and m is not None:
        try:
            m.remove_layer(_hover["layer"])
        except Exception:  # noqa: BLE001
            pass
    _hover["layer"] = None
    _hover["key"] = None


def _update_hover(lat, lon):
    m = _MAP["m"]
    if m is None:
        return
    cand = _pick(lat, lon)
    key = (cand["obj"], cand["id"]) if cand else None
    if key == _hover["key"]:
        return
    _clear_hover()
    _hover["key"] = key
    if not cand:
        return
    if cand["obj"] == "links":
        coords = next((f["geometry"]["coordinates"]
                       for f in (state.map_data.value or {}).get("links", {}).get("features", [])
                       if f["properties"]["id"] == cand["id"]), None)
        if coords:
            _hover["layer"] = L.Polyline(locations=[(y, x) for x, y in coords],
                                         color="#7CFC8A", weight=5, opacity=0.6, fill=False)
    else:
        co = _coords_of(cand)
        if co:
            _hover["layer"] = layers.hover_ring(co[0], co[1])
    if _hover["layer"] is not None:
        m.add_layer(_hover["layer"])


def _drop_grabbed(lat, lon):
    gl, cand = state.grabbed_line.value, _assign["link"]
    sid = state.scenario_id.value
    if not cand:
        state.status.value = "Hover a link to pick a direction, then click."
        return
    try:
        res = api.insert_detector(sid, lat, lon, name=gl["name"], link_id=cand["id"],
                                  snap=state.snap_mode.value, source_video_id=gl["video_id"],
                                  source_line_id=gl["line_id"])
        try:
            api.pull_observations(sid, res.get("id"))
        except Exception:  # noqa: BLE001 — counts may not be ready; volume stays blank
            pass
        state.status.value = f"Assigned '{gl['name']}' → link (volume pulled)."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Assign failed: {e}"
    state.grabbed_line.value = None
    _clear_assign_highlight()
    actions.refresh_map()


def _fetch_detector_info():
    insp = state.inspect_detector.value
    if not insp:
        state.detector_info.value = None
        return
    try:
        state.detector_info.value = api.detector_video_info(state.scenario_id.value, insp["id"])
    except Exception as e:  # noqa: BLE001
        state.detector_info.value = {"reachable": False, "error": str(e)}


def _flip_detector_direction():
    insp = state.inspect_detector.value
    info = state.detector_info.value or {}
    if not insp:
        return
    new = "BA" if (info.get("link_direction") or "AB").upper() == "AB" else "AB"
    sid = state.scenario_id.value
    try:
        api.update_object("detectors", insp["id"], {"link_direction": new})
        try:
            api.pull_observations(sid, insp["id"])
        except Exception:  # noqa: BLE001
            pass
        state.detector_info.value = api.detector_video_info(sid, insp["id"])
        actions.refresh_map()
        state.status.value = f"Flipped count direction → {new}."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Flip failed: {e}"


def _bbox(a, b):
    s, n = sorted([a[0], b[0]])
    w, e = sorted([a[1], b[1]])
    return s, w, n, e


# --- map interaction ------------------------------------------------------ #
def _box_select(a, b):
    s, w, n, e = _bbox(a, b)
    try:
        res = api.select_in_bbox(state.scenario_id.value, s, w, n, e)
    except Exception as ex:  # noqa: BLE001
        state.status.value = f"select failed: {ex}"
        return
    md = state.map_data.value or {}
    many = []
    for obj in ("nodes", "links", "zones", "detectors", "stops"):
        idset = set(res.get(obj, []))
        for f in md.get(obj, {}).get("features", []):
            if f["properties"].get("id") in idset:
                many.append({"obj": obj, "id": f["properties"]["id"], "props": f["properties"]})
    state.selected.value = None
    state.selected_many.value = many
    state.status.value = f"Selected {len(many)} elements."


def _zone_click(lat, lon):
    m = _MAP["m"]
    if _zone["centroid"] is None:
        _zone["centroid"] = (lat, lon)
        _zone["verts"] = []
        state.status.value = "Zone: click boundary vertices; click the first vertex to close."
        return
    verts = _zone["verts"]
    if len(verts) >= 3:
        fv = verts[0]
        if abs(fv[0] - lat) < 3e-4 and abs(fv[1] - lon) < 3e-4:
            _finalize_zone()
            return
    verts.append((lat, lon))
    if m is not None:
        if _zone["line"] is not None:
            try:
                m.remove_layer(_zone["line"])
            except Exception:
                pass
        _zone["line"] = L.Polyline(locations=list(verts), color="#3aa0ff", weight=2, fill=False)
        m.add_layer(_zone["line"])
    state.status.value = f"Zone: {len(verts)} vertices (click first vertex to close)."


def _finalize_zone():
    m, c, verts = _MAP["m"], _zone["centroid"], _zone["verts"]
    poly = [[lon, lat] for (lat, lon) in verts]
    try:
        api.insert_zone(state.scenario_id.value, c[0], c[1], name="zone", polygon=poly)
        state.status.value = f"Zone created ({len(verts)} vertices)."
    except Exception as ex:  # noqa: BLE001
        state.status.value = f"Zone failed: {ex}"
    if m is not None and _zone["line"] is not None:
        try:
            m.remove_layer(_zone["line"])
        except Exception:
            pass
    _zone.update(centroid=None, verts=[], line=None)
    actions.refresh_map()


# --- view helpers (build-from-zero orientation) --------------------------- #
def _fit_network():
    m = _MAP["m"]
    md = state.map_data.value or {}
    if m is None:
        return
    b = layers.bounds_of(md.get("nodes") or {}) or layers.bounds_of(md.get("zones") or {})
    if b:
        m.fit_bounds(b)
    else:
        state.status.value = "Nothing to fit yet — add nodes or search for a place."


def _zoom_to_selection():
    m = _MAP["m"]
    if m is None:
        return
    sels = ([state.selected.value] if state.selected.value else []) + list(state.selected_many.value)
    pts = [co for s in sels if (co := _coords_of(s))]
    if not pts:
        state.status.value = "Select something first."
        return
    if len(pts) == 1:
        lon, lat = pts[0]
        m.center = (lat, lon)
        m.zoom = max(int(getattr(m, "zoom", None) or state.map_zoom.value or 14), 16)
    else:
        lons = [p[0] for p in pts]; lats = [p[1] for p in pts]
        m.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])


def on_interaction(**kw):
    t = kw.get("type")
    lat, lon = (kw.get("coordinates") or (None, None))
    if lat is None:
        return
    m = _MAP["m"]

    if t == "mousemove" and m is not None and getattr(m, "_coords", None) is not None:
        m._coords.value = (f"<div style='background:rgba(20,26,36,.82);color:#cfe0ff;"
                           f"padding:2px 7px;border-radius:6px;font-size:11px;"
                           f"font-family:ui-monospace,monospace'>{lat:.5f}, {lon:.5f}</div>")

    if t == "contextmenu":
        _select(_pick(lat, lon))
        return

    if state.edit_mode.value == "Selection":
        if t == "mousedown":
            _clear_hover()
            _rubber["start"] = (lat, lon)
            return
        if t == "mousemove" and _rubber["start"] is not None and m is not None:
            s, w, n, e = _bbox(_rubber["start"], (lat, lon))
            if _rubber["rect"] is None:
                _rubber["rect"] = L.Rectangle(bounds=[(s, w), (n, e)], color="#ff2d2d",
                                              weight=1, fill_color="#ff2d2d", fill_opacity=0.08)
                m.add_layer(_rubber["rect"])
            else:
                _rubber["rect"].bounds = [(s, w), (n, e)]
            return
        if t == "mousemove":                         # idle hover-highlight under the cursor
            _update_hover(lat, lon)
            return
        if t == "mouseup":
            start = _rubber["start"]
            _rubber["start"] = None
            if m is not None and _rubber["rect"] is not None:
                try:
                    m.remove_layer(_rubber["rect"])
                except Exception:
                    pass
            _rubber["rect"] = None
            if start is None:
                return
            if abs(start[0] - lat) > 1e-5 or abs(start[1] - lon) > 1e-5:
                _box_select(start, (lat, lon))
            else:
                _clear_hover()
                _select(_pick(lat, lon))
            return
        return  # ignore 'click' in Selection mode

    # Creation mode
    # While a counting line is grabbed, track the candidate link under the cursor (red + arrow).
    if state.active_tool.value == "Detector" and state.grabbed_line.value is not None and t == "mousemove":
        _update_assign_highlight(lat, lon)
        return
    # While drawing a link, rubber-band from the pending node to the cursor.
    if state.active_tool.value == "Link" and state.pending_link_from.value is not None and t == "mousemove":
        _update_link_preview(lat, lon)
        return
    if t != "click":
        return
    sid = state.scenario_id.value
    if not sid:
        state.status.value = "Pick a scenario first."
        return
    tool = state.active_tool.value
    if tool == "Zone":
        _zone_click(lat, lon)
        return
    try:
        if tool == "Node":
            api.insert_node(sid, lat, lon); actions.refresh_map()
        elif tool == "Stop":
            api.insert_stop(sid, lat, lon); actions.refresh_map()
        elif tool == "Detector":
            if state.grabbed_line.value is not None:
                _drop_grabbed(lat, lon)
            else:
                state.status.value = "Pick a video, then grab a counting line to assign it to a link."
        elif tool == "Link":
            nid = _node_within(lat, lon, _snap_tol())
            created = False
            if nid is None:                                  # empty space → auto-create the node
                nid = api.insert_node(sid, lat, lon)["id"]
                created = True
            if state.pending_link_from.value is None:
                state.pending_link_from.value = nid
                if created:
                    actions.refresh_map()                    # show the just-created start node
                state.status.value = "Link: click the TO node (empty space makes one). Esc to stop."
            elif nid == state.pending_link_from.value:
                state.status.value = "Link: pick a different TO node."
            else:
                api.insert_link(sid, state.pending_link_from.value, nid,
                                state.link_type_id.value or None, oneway=not state.link_twoway.value)
                _clear_link_preview()
                state.pending_link_from.value = nid if state.link_chain.value else None
                actions.refresh_map()
                state.status.value = ("Link created — click next node (Esc to stop)."
                                      if state.link_chain.value else "Link created.")
        elif tool == "Connector":
            if state.pending_link_from.value is None:
                sel = _nearest_any(lat, lon)
                if sel and sel["obj"] == "zones":
                    state.pending_link_from.value = sel["id"]
                    state.status.value = "Connector: now click a node."
                else:
                    state.status.value = "Connector: first click a zone."
            else:
                api.create_object(sid, "connectors", {"zone_id": state.pending_link_from.value,
                                                       "node_id": _nearest_node(lat, lon), "direction": "both"})
                state.pending_link_from.value = None
                actions.refresh_map(); state.status.value = "Connector created."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"{tool} failed: {e}"


def _commit_move():
    sel, dp = state.selected.value, state.drag_pos.value
    if sel and sel.get("obj") == "nodes" and dp:
        try:
            api.move_node(state.scenario_id.value, sel["id"], dp[1], dp[0])
            actions.refresh_map()
            state.status.value = "Node moved (connected links updated)."
        except Exception as e:  # noqa: BLE001
            state.status.value = f"Move failed: {e}"


def _delete():
    sel = state.selected.value
    if not sel:
        return
    try:
        api.delete_object(sel["obj"], sel["id"])
        state.selected.value = None
        state.drag_pos.value = None
        actions.refresh_map()
        state.status.value = "Deleted."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Delete failed: {e}"


def _save_attrs():
    sel = state.selected.value
    if not sel:
        return
    obj = sel["obj"]
    try:
        api.update_object(obj, sel["id"], lists._coerce(_qv_buf.value))
        actions.refresh_map()
        for f in (state.map_data.value or {}).get(obj, {}).get("features", []):
            if f["properties"].get("id") == sel["id"]:
                _select({"obj": obj, "id": sel["id"], "props": f["properties"]})
                break
        state.status.value = "Saved."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Save failed: {e}"


def _bulk_apply(obj, ids):
    if not _bulk_attr.value:
        return
    try:
        api.bulk_update(obj, ids, lists._coerce({_bulk_attr.value: _bulk_val.value}))
        actions.refresh_map()
        state.status.value = f"Updated {_bulk_attr.value} on {len(ids)} {obj}."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Bulk update failed: {e}"


def _bulk_delete_all():
    many = state.selected_many.value
    by_type = {}
    for s in many:
        by_type.setdefault(s["obj"], []).append(s["id"])
    try:
        for obj, ids in by_type.items():
            api.bulk_delete(obj, ids)
        state.selected_many.value = []
        actions.refresh_map()
        state.status.value = f"Deleted {len(many)} elements."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Bulk delete failed: {e}"


# --- overlays (live widgets) ---------------------------------------------- #
def _passes(props, flt):
    attr = flt.get("attr")
    if not attr:
        return True
    op, val, pv = flt.get("op", ">"), flt.get("value", ""), props.get(flt["attr"])
    try:
        x, y = float(pv), float(val)
        return {">" : x > y, "<": x < y, "=": x == y, "≠": x != y}.get(op, True)
    except (TypeError, ValueError):
        s = "" if pv is None else str(pv)
        if op == "=":
            return s == val
        if op == "≠":
            return s != val
        return True


def _flt(obj, fc):
    flt = state.elem_filter.value
    if not fc or flt.get("obj") != obj or not flt.get("attr"):
        return fc
    return {"type": "FeatureCollection",
            "features": [f for f in fc.get("features", []) if _passes(f.get("properties", {}), flt)]}


def build_overlays():
    md = state.map_data.value or {}
    vis = state.visible_layers.value
    flows = state.flows_fc.value
    ov = []
    if vis.get("links", True):
        if flows:
            ov += layers.flow_link_widgets(_flt("links", flows), state.link_color_by.value)
        elif md.get("links"):
            ov += layers.plain_link_widgets(_flt("links", md["links"]))
    if vis.get("connectors", True) and md.get("connectors"):
        ov += layers.connector_widgets(md["connectors"])
    if vis.get("lines", True) and md.get("lines"):
        ov += layers.line_widgets(md["lines"])
    if vis.get("desire", True) and _desire_cache.value and md.get("zones"):
        dv = _desire_cache.value
        ov += layers.desire_widgets(md["zones"], dv["labels"], dv["values"])
    if vis.get("nodes", True) and md.get("nodes"):
        ov += layers.node_widgets(_flt("nodes", md["nodes"]))
    if vis.get("zones", True) and md.get("zones"):
        ov += layers.zone_widgets(_flt("zones", md["zones"]))
    if vis.get("stops", True) and md.get("stops"):
        ov += layers.stop_widgets(md["stops"])
    if vis.get("detectors", True) and md.get("detectors"):
        ov += layers.detector_widgets(md["detectors"])
    if state.show_labels.value:                      # name labels (capped for performance)
        lab = [md[k] for k in ("nodes", "links", "zones") if vis.get(k, True) and md.get(k)]
        if sum(len(fc.get("features", [])) for fc in lab) <= 300:
            for fc in lab:
                ov += layers.label_widgets(fc)
    for s in state.selected_many.value:
        co = _coords_of(s)
        if co:
            ov.append(layers.selected_ring(co[0], co[1]))
    sel = state.selected.value
    if sel:
        co = _coords_of(sel)
        if co:
            ov.append(layers.selected_ring(co[0], co[1]))
        if sel["obj"] == "nodes" and state.drag_pos.value:
            dp = state.drag_pos.value
            mk = L.Marker(location=(dp[1], dp[0]), draggable=True)
            mk.observe(lambda ch: state.drag_pos.set([ch["new"][1], ch["new"][0]]), "location")
            ov.append(mk)
    insp = state.inspect_detector.value
    if insp and state.detector_info.value is not None:
        ov.append(layers.detector_popup(state.detector_info.value, insp["lat"], insp["lon"],
                                         on_flip=_flip_detector_direction))
    return ov


def _make_map():
    m = L.Map(center=state.map_center.value, zoom=state.map_zoom.value,
              basemap=L.basemaps.CartoDB.DarkMatter, scroll_wheel_zoom=True,
              double_click_zoom=False, box_zoom=False, dragging=True)
    m.layout.height = "84vh"
    m.on_interaction(on_interaction)
    legend = W.HTML(value=layers.legend_html(state.link_color_by.value, False))
    m.add(L.WidgetControl(widget=legend, position="bottomright"))
    m._legend = legend
    coords = W.HTML(value="")
    m.add(L.WidgetControl(widget=coords, position="bottomleft"))
    m._coords = coords
    try:                                            # geocoder: jump to a real place to build over
        m.add(L.SearchControl(position="topleft", zoom=16,
                              url="https://nominatim.openstreetmap.org/search?format=json&q={s}"))
    except Exception:  # noqa: BLE001 — older ipyleaflet / offline: skip gracefully
        pass
    _MAP["m"] = m
    return m


def _load_desire():
    mid = state.desire_matrix_id.value
    if not mid:
        _desire_cache.value = None
        return
    try:
        _desire_cache.value = api.matrix_values(mid)
    except Exception:  # noqa: BLE001
        _desire_cache.value = None


# --- panels --------------------------------------------------------------- #
def _set_mode(v):
    state.edit_mode.value = v
    state.selected_many.value = []
    _zone.update(centroid=None, verts=[], line=None)
    if v == "Creation" and state.active_tool.value == "Select":
        state.active_tool.value = "Node"
    state.pending_link_from.value = None
    _clear_link_preview()
    _clear_hover()
    if v != "Creation":
        state.grabbed_line.value = None
        _clear_assign_highlight()


def _set_tool(t):
    """Switch the active creation tool, abandoning any in-progress draw."""
    state.active_tool.value = t
    state.pending_link_from.value = None
    _clear_link_preview()
    _zone.update(centroid=None, verts=[], line=None)


def _set_filter(k, v):
    state.elem_filter.set({**state.elem_filter.value, k: v})


def _select_matching():
    """Add every element passing the Display filter to the multi-selection."""
    flt = state.elem_filter.value
    obj = flt.get("obj", "links")
    if not flt.get("attr"):
        state.status.value = "Set a filter attribute first."
        return
    md = state.map_data.value or {}
    many = [{"obj": obj, "id": f["properties"]["id"], "props": f["properties"]}
            for f in md.get(obj, {}).get("features", []) if _passes(f.get("properties", {}), flt)]
    state.selected.value = None
    state.selected_many.value = many
    state.status.value = f"Selected {len(many)} {obj} matching the filter."


@solara.component
def DetectorPanel():
    """Detector tool: assign a counter video's lines onto links (drag-to-link, Visum-style)."""
    solara.Select("Marker snaps to", value=state.snap_mode, values=["node", "link"])
    src = state.counter_sources.value
    if src is None:
        solara.Button("Load videos", icon_name="mdi-cctv", on_click=_load_counter_sources, block=True)
        return
    if not src.get("reachable", False):
        solara.Markdown(f"*Counter offline.* `{str(src.get('error', ''))[:36]}`")
        solara.Button("Retry", text=True, on_click=_load_counter_sources)
        return
    projects = src.get("projects", [])
    if not projects:
        solara.Markdown("*No counter projects.*")
        solara.Button("Reload", text=True, on_click=_load_counter_sources)
        return

    p_by = {(p.get("name") or p["project_id"][:6]): p["project_id"] for p in projects}
    p_cur = next((n for n, i in p_by.items() if i == state.det_project_id.value), list(p_by)[0])
    solara.Select("Project", value=p_cur, values=list(p_by),
                  on_value=lambda n: state.det_project_id.set(p_by[n]))
    proj = next((p for p in projects if p["project_id"] == p_by.get(p_cur)), projects[0])

    videos = proj.get("videos", [])
    if not videos:
        solara.Markdown("*No videos in this project.*")
        return
    v_by = {f"{v.get('filename') or v['video_id'][:6]} · {v.get('status', '?')}": v["video_id"]
            for v in videos}
    v_cur = next((n for n, i in v_by.items() if i == state.det_video_id.value), list(v_by)[0])
    solara.Select("Video", value=v_cur, values=list(v_by),
                  on_value=lambda n: state.det_video_id.set(v_by[n]))
    vid = next((v for v in videos if v["video_id"] == v_by.get(v_cur)), videos[0])

    md = state.map_data.value or {}
    assigned = {f["properties"].get("source_line_id")
                for f in md.get("detectors", {}).get("features", [])
                if f["properties"].get("source_line_id")}
    grabbed = state.grabbed_line.value
    lines = vid.get("lines", [])
    solara.Markdown("**Counting lines** — grab one, then drop on a link")
    if not lines:
        solara.Markdown("*This video has no counting lines yet.*")
    for ln in lines:
        lid = ln["line_id"]
        lname = ln.get("name") or lid[:6]
        is_grabbed = bool(grabbed and grabbed.get("line_id") == lid)
        badge = "●" if lid in assigned else "○"
        label = f"{badge} {lname}" + (" …drop it" if is_grabbed else "")
        solara.Button(label, block=True, text=not is_grabbed,
                      color="primary" if is_grabbed else None,
                      on_click=lambda v=vid["video_id"], i=lid, n=lname: _grab_line(v, i, n))
    if grabbed:
        solara.Markdown("*Hover a link (red arrow shows direction), then click to drop.*")
        solara.Button("Cancel assign", text=True, on_click=_cancel_grab)


@solara.component
def Toolbar():
    sid = state.scenario_id.value
    lts = solara.use_memo(lambda: api.list_objects(sid, "link_types") if sid else [], [sid])
    solara.Markdown("**Network**")
    solara.ToggleButtonsSingle(value=state.edit_mode.value, values=["Selection", "Creation"],
                               on_value=_set_mode)
    if state.edit_mode.value == "Creation":
        solara.Markdown("*Insert element*")
        for t in TOOLS[1:]:
            active = state.active_tool.value == t
            solara.Button(t, icon_name=theme.TOOL_ICONS.get(t),
                          on_click=lambda t=t: _set_tool(t),
                          color="primary" if active else None, text=not active, block=True)
        if state.active_tool.value == "Link":
            if lts:
                id_by = {x["name"]: x["id"] for x in lts}
                cur = next((n for n, i in id_by.items() if i == state.link_type_id.value), lts[0]["name"])
                solara.Select("Link type", value=cur, values=list(id_by),
                              on_value=lambda n: state.link_type_id.set(id_by[n]))
            solara.Switch(label="Two-way", value=state.link_twoway.value, on_value=state.link_twoway.set)
            solara.Switch(label="Chain", value=state.link_chain.value, on_value=state.link_chain.set)
            solara.Markdown("*Click nodes to link; empty space makes a node.*")
        if state.active_tool.value == "Detector":
            DetectorPanel()
        if state.active_tool.value == "Zone":
            solara.Markdown("*Click a centroid, then boundary vertices; click the first to close.*")
        if state.pending_link_from.value is not None:
            solara.Button("Cancel (Esc)", text=True, on_click=_cancel_pending)
    else:
        solara.Markdown("*Drag a box to multi-select · click selects · right-click inspects*")


@solara.component
def DisplayPanel(sid):
    solara.Markdown("**Display**")
    solara.Select("Basemap", value=state.basemap, values=["Dark", "Aerial", "OSM"])
    with solara.Row():
        solara.Button("Fit network", text=True, on_click=_fit_network)
        solara.Button("Zoom to sel.", text=True, on_click=_zoom_to_selection)
    solara.Select("Colour links by", value=state.link_color_by, values=["GEH", "Volume", "V/C"])
    mats = solara.use_memo(
        lambda: [m for m in (api.list_matrices(sid) if sid else []) if m.get("kind") == "demand"],
        [sid, state.map_data.value is not None])
    by = {f"{m['name']} · {m['id'][:6]}": m["id"] for m in mats}
    cur = next((l for l, i in by.items() if i == state.desire_matrix_id.value), "(none)")
    solara.Select("Desire lines", value=cur, values=["(none)"] + list(by),
                  on_value=lambda l: state.desire_matrix_id.set(by.get(l, "")))

    solara.Markdown("**Layers**")
    vis = state.visible_layers.value
    for k in ("links", "nodes", "zones", "connectors", "stops", "lines", "detectors", "desire"):
        solara.Checkbox(label=k, value=vis.get(k, True),
                        on_value=lambda nv, k=k: state.visible_layers.set({**state.visible_layers.value, k: nv}))
    solara.Checkbox(label="name labels", value=state.show_labels.value, on_value=state.show_labels.set)

    solara.Markdown("**Filter** (hide non-matching)")
    flt = state.elem_filter.value
    solara.Select("Object", value=flt.get("obj", "links"), values=list(FILTER_ATTRS),
                  on_value=lambda v: _set_filter("obj", v))
    solara.Select("Attribute", value=flt.get("attr", ""), values=[""] + FILTER_ATTRS.get(flt.get("obj", "links"), []),
                  on_value=lambda v: _set_filter("attr", v))
    with solara.Row():
        solara.Select("Op", value=flt.get("op", ">"), values=[">", "<", "=", "≠"],
                      on_value=lambda v: _set_filter("op", v))
        solara.InputText("Value", value=flt.get("value", ""), on_value=lambda v: _set_filter("value", v))
    if flt.get("attr"):
        with solara.Row():
            solara.Button("Select matching", text=True, on_click=_select_matching)
            solara.Button("Clear filter", text=True,
                          on_click=lambda: state.elem_filter.set({"obj": "links", "attr": "", "op": ">", "value": ""}))

    md = state.map_data.value or {}
    counts = " · ".join(f"{k}:{len(md.get(k, {}).get('features', []))}"
                        for k in ("nodes", "links", "zones", "detectors"))
    solara.Markdown(counts)


@solara.component
def MultiView(many):
    by_type = {}
    for s in many:
        by_type.setdefault(s["obj"], []).append(s["id"])
    solara.Markdown("### Selection\n" + " · ".join(f"**{k}**: {len(v)}" for k, v in by_type.items()))
    types = list(by_type)
    bt = _bulk_type.value if _bulk_type.value in types else types[0]
    solara.Select("Edit type", value=bt, values=types, on_value=_bulk_type.set)
    fields = [f for f in lists.FIELDS.get(bt, []) if f not in ("connector_node_id",)]
    if fields:
        ba = _bulk_attr.value if _bulk_attr.value in fields else fields[0]
        solara.Select("Attribute", value=ba, values=fields, on_value=_bulk_attr.set)
        solara.InputText("Value", value=_bulk_val)
        solara.Button(f"Apply to {len(by_type[bt])} {bt}", color="primary",
                      on_click=lambda: _bulk_apply(bt, by_type[bt]))
    with solara.Row():
        solara.Button(f"Delete all ({len(many)})", color="error", on_click=_bulk_delete_all)
        solara.Button("Clear", text=True, on_click=lambda: state.selected_many.set([]))


@solara.component
def QuickView():
    solara.Markdown("### Quick view")
    sid = state.scenario_id.value
    many = state.selected_many.value
    sel = state.selected.value
    obj = sel["obj"] if sel else None
    # Hooks must run before the early returns (stable hook order). Link types feed the
    # link_type_id dropdown and its default-inheritance.
    lt_rows = solara.use_memo(
        lambda: (api.list_objects(sid, "link_types") if (sid and obj == "links") else []),
        [sid, obj, state.data_version.value])
    if many:
        MultiView(many)
        return
    if not sel:
        solara.Markdown("*Drag a box (Selection mode) to multi-select; click or right-click to inspect one.*")
        return

    def set_field(f, nv):
        _qv_buf.set({**_qv_buf.value, f: nv})

    def set_link_type(ltid):
        buf = {**_qv_buf.value, "link_type_id": ltid}
        row = next((r for r in lt_rows if r.get("id") == ltid), None)
        if row:                                  # inherit type defaults (override before Save)
            for dst, src in (("lanes", "num_lanes"), ("v0_kmh", "v0_kmh"), ("capacity_vph", "capacity_vph")):
                if row.get(src) is not None:
                    buf[dst] = row[src]
        _qv_buf.set(buf)

    solara.Markdown(f"**{obj[:-1]}** · `{sel['id'][:8]}`")
    for f in lists.FIELDS.get(obj, []):
        v = _qv_buf.value.get(f)
        if f == "link_type_id" and obj == "links":
            lists.FkSelect(f, v, set_link_type, sid)
        elif f in lists.FK_REGISTRY:
            lists.FkSelect(f, v, lambda nv, f=f: set_field(f, nv), sid)
        elif isinstance(v, bool):
            solara.Switch(label=f, value=v, on_value=lambda nv, f=f: set_field(f, nv))
        else:
            solara.InputText(f, value="" if v is None else str(v),
                             on_value=lambda nv, f=f: set_field(f, nv))
    with solara.Row():
        solara.Button("Save", color="primary", on_click=_save_attrs)
        solara.Button("Delete", color="error", on_click=_delete)
    if obj == "nodes":
        solara.Markdown("*Drag the pin on the map, then:*")
        solara.Button("Commit move", on_click=_commit_move)


@solara.component
def Section():
    sid = state.scenario_id.value
    if sid and state.map_data.value is None:
        actions.refresh_map()

    m = solara.use_memo(_make_map, [])
    solara.use_effect(_load_desire, [state.desire_matrix_id.value])

    def apply_mode():
        m.dragging = (state.edit_mode.value != "Selection")  # Selection → rubber-band, not pan
    solara.use_effect(apply_mode, [state.edit_mode.value])

    def apply_basemap():
        others = [l for l in m.layers if not isinstance(l, L.TileLayer)]
        m.layers = (layers.base_tile(state.basemap.value),) + tuple(others)
    solara.use_effect(apply_basemap, [state.basemap.value])

    def sync():
        base = [l for l in m.layers if isinstance(l, L.TileLayer)] or [layers.base_tile(state.basemap.value)]
        m.layers = tuple(base) + tuple(build_overlays())
        if getattr(m, "_legend", None) is not None:
            m._legend.value = layers.legend_html(state.link_color_by.value, state.flows_fc.value is not None)
    solara.use_effect(sync, [state.map_data.value, state.flows_fc.value, state.selected.value,
                             state.selected_many.value, state.visible_layers.value,
                             state.link_color_by.value, state.desire_matrix_id.value,
                             state.elem_filter.value, _desire_cache.value, state.show_labels.value,
                             state.inspect_detector.value, state.detector_info.value])
    solara.use_effect(_fetch_detector_info, [state.inspect_detector.value])

    def apply_view():
        m.center = state.map_center.value
        m.zoom = state.map_zoom.value
    solara.use_effect(apply_view, [state.map_center.value, state.map_zoom.value])

    with solara.Row(style={"height": "86vh", "gap": "8px"}):
        with solara.Column(classes=["mm-rail"], style={"overflow-y": "auto"}):
            Toolbar()
            DisplayPanel(sid)
        with solara.Column(style={"flex": "1"}):
            solara.display(m)
        with solara.Column(classes=["mm-panel"], style={"min-width": "250px", "max-width": "300px",
                                                        "overflow-y": "auto"}):
            QuickView()
