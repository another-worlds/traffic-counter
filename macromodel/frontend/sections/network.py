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
PLACEHOLDERS = ["Turns", "Main nodes", "Territories", "OD pairs", "PrT paths", "POIs"]

_desire_cache = solara.reactive(None)
_qv_buf = solara.reactive({})
_qv_id = solara.reactive("")
_bulk_type = solara.reactive("")
_bulk_attr = solara.reactive("")
_bulk_val = solara.reactive("")

_MAP = {"m": None}                                   # the stable map widget
_rubber = {"start": None, "rect": None}              # rubber-band box (Selection drag)
_zone = {"centroid": None, "verts": [], "line": None}  # zone polygon being drawn

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


def on_interaction(**kw):
    t = kw.get("type")
    lat, lon = (kw.get("coordinates") or (None, None))
    if lat is None:
        return
    m = _MAP["m"]

    if t == "contextmenu":
        _select(_nearest_any(lat, lon))
        return

    if state.edit_mode.value == "Selection":
        if t == "mousedown":
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
                _select(_nearest_any(lat, lon))
            return
        return  # ignore 'click' in Selection mode

    # Creation mode
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
            api.insert_detector(sid, lat, lon, link_direction=state.direction.value)
            actions.refresh_map(); state.status.value = "Detector placed (snapped to nearest link)."
        elif tool == "Link":
            nn = _nearest_node(lat, lon)
            if state.pending_link_from.value is None:
                state.pending_link_from.value = nn
                state.status.value = "Link: now click the TO node."
            else:
                api.insert_link(sid, state.pending_link_from.value, nn, state.link_type_id.value or None)
                state.pending_link_from.value = None
                actions.refresh_map(); state.status.value = "Link created."
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


def _set_filter(k, v):
    state.elem_filter.set({**state.elem_filter.value, k: v})


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
                          on_click=lambda t=t: state.active_tool.set(t),
                          color="primary" if active else None, text=not active, block=True)
        if state.active_tool.value == "Link" and lts:
            id_by = {x["name"]: x["id"] for x in lts}
            cur = next((n for n, i in id_by.items() if i == state.link_type_id.value), lts[0]["name"])
            solara.Select("Link type", value=cur, values=list(id_by),
                          on_value=lambda n: state.link_type_id.set(id_by[n]))
        if state.active_tool.value == "Detector":
            solara.Select("Direction", value=state.direction, values=["AB", "BA"])
        if state.active_tool.value == "Zone":
            solara.Markdown("*Click a centroid, then boundary vertices; click the first to close.*")
    else:
        solara.Markdown("*Drag a box to multi-select · click selects · right-click inspects*")
    solara.Markdown("*Not editable yet*")
    for t in PLACEHOLDERS:
        solara.Button(t, disabled=True, text=True, block=True)


@solara.component
def DisplayPanel(sid):
    solara.Markdown("**Display**")
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
    many = state.selected_many.value
    if many:
        MultiView(many)
        return
    sel = state.selected.value
    if not sel:
        solara.Markdown("*Drag a box (Selection mode) to multi-select; click or right-click to inspect one.*")
        return
    obj = sel["obj"]
    solara.Markdown(f"**{obj[:-1]}** · `{sel['id'][:8]}`")
    for f in lists.FIELDS.get(obj, []):
        v = _qv_buf.value.get(f)
        if isinstance(v, bool):
            solara.Switch(label=f, value=v, on_value=lambda nv, f=f: _qv_buf.set({**_qv_buf.value, f: nv}))
        else:
            solara.InputText(f, value="" if v is None else str(v),
                             on_value=lambda nv, f=f: _qv_buf.set({**_qv_buf.value, f: nv}))
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

    def sync():
        base = [l for l in m.layers if isinstance(l, L.TileLayer)] or [layers.dark_tile()]
        m.layers = tuple(base) + tuple(build_overlays())
        if getattr(m, "_legend", None) is not None:
            m._legend.value = layers.legend_html(state.link_color_by.value, state.flows_fc.value is not None)
    solara.use_effect(sync, [state.map_data.value, state.flows_fc.value, state.selected.value,
                             state.selected_many.value, state.visible_layers.value,
                             state.link_color_by.value, state.desire_matrix_id.value,
                             state.elem_filter.value, _desire_cache.value])

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
