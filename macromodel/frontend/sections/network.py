"""Network section: a Visum-style element toolbar, an ipyleaflet map with click-to-
insert tools, and an attribute panel for the selected element.
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


def on_map_click(lat, lon):
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
                nn = _nearest_node(lat, lon)
                api.create_object(sid, "connectors",
                                  {"zone_id": state.pending_link_from.value, "node_id": nn, "direction": "both"})
                state.pending_link_from.value = None
                actions.refresh_map(); state.status.value = "Connector created."
        else:  # Select
            state.selected.value = _nearest_any(lat, lon)
    except Exception as e:  # noqa: BLE001
        state.status.value = f"{tool} failed: {e}"


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
    if sel and sel.get("props"):
        coords = _coords_of(sel)
        if coords:
            ov.append(layers.selected_marker(coords[0], coords[1]))
    return ov


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


@solara.component
def Toolbar():
    sid = state.scenario_id.value
    lts = solara.use_memo(
        lambda: api.list_objects(sid, "link_types") if sid else [], [sid])
    solara.Markdown("**Tools**")
    solara.ToggleButtonsSingle(value=state.active_tool, values=TOOLS)
    if state.active_tool.value == "Link" and lts:
        id_by = {t["name"]: t["id"] for t in lts}
        cur = next((n for n, i in id_by.items() if i == state.link_type_id.value), lts[0]["name"])
        solara.Select("Link type", value=cur, values=list(id_by),
                      on_value=lambda n: state.link_type_id.set(id_by[n]))
    if state.active_tool.value == "Detector":
        solara.Select("Direction", value=state.direction, values=["AB", "BA"])
    solara.Markdown("---")
    md = state.map_data.value or {}
    counts = {k: len(md.get(k, {}).get("features", [])) for k in
              ("nodes", "links", "zones", "connectors", "stops", "lines", "detectors")}
    solara.Markdown("**Counts**\n\n" + "\n".join(f"- {k}: {v}" for k, v in counts.items()))


@solara.component
def AttributePanel():
    sel = state.selected.value
    if not sel:
        solara.Markdown("*Select tool: click an element to inspect/delete it.*")
        return
    solara.Markdown(f"### {sel['obj'][:-1]}\n`{sel['id'][:8]}`")
    props = sel.get("props", {})
    solara.Markdown("\n".join(f"- **{k}**: {v}" for k, v in props.items() if k not in ("id", "kind")))

    def do_delete():
        try:
            api.delete_object(sel["obj"], sel["id"])
            state.selected.value = None
            actions.refresh_map()
            state.status.value = "Deleted."
        except Exception as e:  # noqa: BLE001
            state.status.value = f"Delete failed: {e}"

    solara.Button("Delete element", on_click=do_delete, color="error")


@solara.component
def Section():
    if state.scenario_id.value and state.map_data.value is None:
        actions.refresh_map()
    overlays = build_overlays()
    with solara.Row(style={"height": "82vh"}):
        with solara.Column(style={"min-width": "160px", "max-width": "180px"}):
            Toolbar()
        with solara.Column(style={"flex": "1"}):
            map_el = L.Map.element(center=state.map_center.value, zoom=state.map_zoom.value,
                                   scroll_wheel_zoom=True, layout=W.Layout(height="80vh"), layers=overlays)

            def _attach():
                w = get_widget(map_el)

                def h(**kw):
                    if kw.get("type") == "click":
                        c = kw.get("coordinates") or (None, None)
                        if c[0] is not None:
                            on_map_click(c[0], c[1])

                w.on_interaction(h)
                return lambda: w.on_interaction(h, remove=True)

            solara.use_effect(_attach, [])
        with solara.Column(style={"min-width": "240px", "max-width": "270px"}):
            AttributePanel()
