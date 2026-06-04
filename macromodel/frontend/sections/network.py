"""Network section — a Visum-style editor.

Left: a vertical element toolbar. Centre: the ipyleaflet map (pan/zoom preserved via
two-way center/zoom binding — fixes the click-resets-scale bug). Right: an always-on
"Quick view" inspector. LMB with the Select tool (or RMB anywhere) selects the nearest
element, which glows red; a selected node gets a draggable handle you can move + commit.
"""
from __future__ import annotations

import ipyleaflet as L
import ipywidgets as W
import solara

import actions
import api_client as api
import layers
import state

try:
    from solara import get_widget
except Exception:  # pragma: no cover
    from reacton.core import get_widget

TOOLS = ["Select", "Node", "Link", "Zone", "Connector", "Stop", "Detector"]
# Visum object types we don't edit yet — shown disabled to mirror the full object list.
PLACEHOLDERS = ["Turns", "Main nodes", "Territories", "OD pairs", "PrT paths", "POIs"]


def _set_tool(t):
    state.active_tool.value = t
    state.pending_link_from.value = None


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
    for obj in ("nodes", "zones", "detectors", "stops"):
        for f in md.get(obj, {}).get("features", []):
            g = f["geometry"]
            if g.get("type") != "Point":
                continue
            x, y = g["coordinates"]
            d = (x - lon) ** 2 + (y - lat) ** 2
            if d < bd:
                bd, best = d, {"obj": obj, "id": f["properties"]["id"], "props": f["properties"]}
    for f in md.get("links", {}).get("features", []):
        cs = f["geometry"]["coordinates"]
        mx, my = (cs[0][0] + cs[-1][0]) / 2, (cs[0][1] + cs[-1][1]) / 2
        d = (mx - lon) ** 2 + (my - lat) ** 2
        if d < bd:
            bd, best = d, {"obj": "links", "id": f["properties"]["id"], "props": f["properties"]}
    return best


def _coords_of(sel):
    md = state.map_data.value or {}
    for f in md.get(sel["obj"], {}).get("features", []):
        if f["properties"].get("id") == sel["id"]:
            g = f["geometry"]
            if g["type"] == "Point":
                return g["coordinates"]
            cs = g["coordinates"]
            return [(cs[0][0] + cs[-1][0]) / 2, (cs[0][1] + cs[-1][1]) / 2]
    return None


def _select(sel):
    state.selected.value = sel
    state.drag_pos.value = _coords_of(sel) if (sel and sel["obj"] == "nodes") else None


def on_interaction(**kw):
    t = kw.get("type")
    c = kw.get("coordinates") or (None, None)
    lat, lon = c
    if lat is None:
        return
    if t == "contextmenu":            # right-click → inspect, regardless of tool
        _select(_nearest_any(lat, lon))
        return
    if t != "click":
        return

    sid = state.scenario_id.value
    if not sid:
        state.status.value = "Pick a scenario first."
        return
    tool = state.active_tool.value
    try:
        if tool == "Node":
            api.insert_node(sid, lat, lon); actions.refresh_map()
        elif tool == "Zone":
            api.insert_zone(sid, lat, lon); actions.refresh_map()
        elif tool == "Stop":
            api.insert_stop(sid, lat, lon); actions.refresh_map()
        elif tool == "Detector":
            api.insert_detector(sid, lat, lon, link_direction=state.direction.value); actions.refresh_map()
            state.status.value = "Detector placed (snapped to nearest link)."
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
                api.create_object(sid, "connectors",
                                  {"zone_id": state.pending_link_from.value, "node_id": _nearest_node(lat, lon),
                                   "direction": "both"})
                state.pending_link_from.value = None
                actions.refresh_map(); state.status.value = "Connector created."
        else:  # Select
            _select(_nearest_any(lat, lon))
    except Exception as e:  # noqa: BLE001
        state.status.value = f"{tool} failed: {e}"


def _on_drag(loc):
    state.drag_pos.value = [loc[1], loc[0]]  # (lat,lon) → [lon,lat]


def _commit_move():
    sel, dp = state.selected.value, state.drag_pos.value
    if sel and sel.get("obj") == "nodes" and dp:
        try:
            api.move_node(state.scenario_id.value, sel["id"], dp[1], dp[0])
            actions.refresh_map()
            _select({**sel, "props": {**sel["props"]}})  # keep selection
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


def build_overlays():
    md = state.map_data.value or {}
    ov = [layers.tile_layer_element()]
    if md.get("links"):
        ov += layers.map_link_elements(md["links"])
    if state.flows_fc.value:
        ov += layers.flows_elements(state.flows_fc.value)
    if md.get("connectors"):
        ov += layers.connector_elements(md["connectors"])
    if md.get("lines"):
        ov += layers.line_elements(md["lines"])
    if md.get("nodes"):
        ov += layers.map_node_elements(md["nodes"])
    if md.get("zones"):
        ov += layers.zone_elements(md["zones"])
    if md.get("stops"):
        ov += layers.stop_elements(md["stops"])
    if md.get("detectors"):
        ov += layers.detector_elements(md["detectors"])
    sel = state.selected.value
    if sel:
        coords = _coords_of(sel)
        if coords:
            ov.append(layers.selected_marker(coords[0], coords[1]))
        if sel["obj"] == "nodes" and state.drag_pos.value:
            dp = state.drag_pos.value
            ov.append(L.Marker.element(location=(dp[1], dp[0]), draggable=True, on_location=_on_drag))
    return ov


@solara.component
def Toolbar():
    sid = state.scenario_id.value
    lts = solara.use_memo(lambda: api.list_objects(sid, "link_types") if sid else [], [sid])
    solara.Markdown("**Network**")
    for t in TOOLS:
        active = state.active_tool.value == t
        solara.Button(t, on_click=lambda t=t: _set_tool(t), color="primary" if active else None,
                      text=not active, block=True, dense=True)
    if state.active_tool.value == "Link" and lts:
        id_by = {x["name"]: x["id"] for x in lts}
        cur = next((n for n, i in id_by.items() if i == state.link_type_id.value), lts[0]["name"])
        solara.Select("Link type", value=cur, values=list(id_by),
                      on_value=lambda n: state.link_type_id.set(id_by[n]))
    if state.active_tool.value == "Detector":
        solara.Select("Direction", value=state.direction, values=["AB", "BA"])
    solara.Markdown("—")
    for t in PLACEHOLDERS:
        solara.Button(t, disabled=True, text=True, block=True, dense=True)
    md = state.map_data.value or {}
    counts = {k: len(md.get(k, {}).get("features", [])) for k in
              ("nodes", "links", "zones", "connectors", "stops", "lines", "detectors")}
    solara.Markdown("**Counts**\n\n" + "\n".join(f"- {k}: {v}" for k, v in counts.items()))


@solara.component
def QuickView():
    solara.Markdown("### Quick view")
    sel = state.selected.value
    if not sel:
        solara.Markdown("*LMB (Select tool) or **right-click** an element to inspect it.*")
        return
    solara.Markdown(f"**{sel['obj'][:-1]}** · `{sel['id'][:8]}`")
    for k, v in sel.get("props", {}).items():
        if k in ("id", "kind"):
            continue
        solara.Markdown(f"- **{k}**: {v}")
    if sel["obj"] == "nodes":
        solara.Markdown("*Drag the pin to move; then commit.*")
        solara.Button("Commit move", on_click=_commit_move, color="primary", dense=True)
    solara.Button("Delete element", on_click=_delete, color="error", dense=True)


@solara.component
def Section():
    if state.scenario_id.value and state.map_data.value is None:
        actions.refresh_map()
    overlays = build_overlays()
    with solara.Row(style={"height": "84vh", "gap": "6px"}):
        with solara.Column(style={"min-width": "150px", "max-width": "168px", "overflow-y": "auto"}):
            Toolbar()
        with solara.Column(style={"flex": "1"}):
            map_el = L.Map.element(
                center=state.map_center.value, zoom=state.map_zoom.value, scroll_wheel_zoom=True,
                layout=W.Layout(height="82vh"), layers=overlays,
                on_center=lambda c: state.map_center.set(tuple(c)),
                on_zoom=lambda z: state.map_zoom.set(z))

            def _attach():
                w = get_widget(map_el)
                w.on_interaction(on_interaction)
                return lambda: w.on_interaction(on_interaction, remove=True)

            solara.use_effect(_attach, [])
        with solara.Column(style={"min-width": "230px", "max-width": "270px", "overflow-y": "auto"}):
            QuickView()
