"""MacroModel — Solara + ipyleaflet front end.

A single map with a step sidebar mirroring the macroscopic pipeline:
scenario → network → zones → georeferenced counters → 4-step → calibration.

The map is created *inside* the Page component and its layers are driven declaratively
from reactive state (Solara closes any widget created at import time, so a module-level
Map would be dead on arrival). Layer data lives in reactive FeatureCollections; the click
handler is attached to the rendered map widget via use_effect.
"""
from __future__ import annotations

import ipyleaflet as L
import ipywidgets as W
import solara

import api_client as api
import layers

try:  # Solara re-exports reacton's get_widget; fall back just in case.
    from solara import get_widget
except Exception:  # pragma: no cover
    from reacton.core import get_widget

# --- reactive UI state ----------------------------------------------------- #
scenario_id = solara.reactive("")
status = solara.reactive("Load the demo scenario, or create one and import a network.")
metrics_md = solara.reactive("")
new_name = solara.reactive("My scenario")
beta = solara.reactive(0.1)
place_mode = solara.reactive(False)
direction = solara.reactive("AB")
counter_seq = solara.reactive(0)
source_labels = solara.reactive([])
selected_source_label = solara.reactive("")

# Map view + layer data (reactive → re-renders the map declaratively).
center = solara.reactive((41.31, 69.27))
zoom = solara.reactive(12)
network_fc = solara.reactive(None)
zones_fc = solara.reactive(None)
counters_fc = solara.reactive(None)
flows_fc = solara.reactive(None)

_source_by_label: dict = {}  # label -> {"video_id", "line_id"}


# --- helpers --------------------------------------------------------------- #
def _recenter(fc: dict) -> None:
    b = layers.bounds_of(fc)
    if not b:
        return
    (s, w), (n, e) = b
    center.value = ((s + n) / 2.0, (w + e) / 2.0)
    zoom.value = 14


def _reload_overlays(recenter: bool = True) -> None:
    sid = scenario_id.value
    if not sid:
        return
    net = api.get_network(sid)
    network_fc.value = net
    zones_fc.value = api.get_zones(sid)
    counters_fc.value = api.get_counters(sid)
    flows_fc.value = None
    if recenter:
        _recenter(net)


def _show_metrics(title: str, m: dict) -> None:
    metrics_md.value = (
        f"**{title}**\n\n"
        f"- mean GEH: **{(m.get('mean_geh') or 0):.2f}**\n"
        f"- GEH &lt; 5: **{(m.get('pct_geh_lt5') or 0):.0f}%**\n"
        f"- RMSE: **{(m.get('rmse') or 0):.0f}** vph\n"
    )


# --- actions (set reactive state; the map repaints itself) ----------------- #
def on_load_demo() -> None:
    try:
        sc = api.create_demo()
        scenario_id.value = sc["id"]
        _reload_overlays()
        status.value = (f"Demo loaded: {sc['n_nodes']} nodes, {sc['n_links']} links, "
                        f"{sc['n_zones']} zones, {sc['n_counters']} counters. Now run the 4-step model.")
    except Exception as e:  # noqa: BLE001
        status.value = f"Demo failed: {e}"


def on_create_sample() -> None:
    try:
        sc = api.create_scenario(new_name.value)
        scenario_id.value = sc["id"]
        api.load_sample(sc["id"])
        api.auto_zones(sc["id"], n=8)
        _reload_overlays()
        status.value = "Scenario created with sample network + 8 zones. Add counters or run the model."
    except Exception as e:  # noqa: BLE001
        status.value = f"Create failed: {e}"


def on_auto_zones() -> None:
    try:
        api.auto_zones(scenario_id.value, n=8)
        zones_fc.value = api.get_zones(scenario_id.value)
        status.value = "Auto-generated 8 zones."
    except Exception as e:  # noqa: BLE001
        status.value = f"Auto-zones failed: {e}"


def on_load_sources() -> None:
    try:
        data = api.counter_sources()
        labels, mapping = [], {}
        for proj in data.get("projects", []):
            for v in proj.get("videos", []):
                for ln in v.get("lines", []):
                    lbl = f"{proj.get('name')} / {v.get('filename')} / {ln.get('name')}"
                    labels.append(lbl)
                    mapping[lbl] = {"video_id": v["video_id"], "line_id": ln["line_id"]}
        _source_by_label.clear()
        _source_by_label.update(mapping)
        source_labels.value = labels
        if labels:
            selected_source_label.value = labels[0]
            status.value = f"Loaded {len(labels)} counting lines from the traffic-counter."
        else:
            status.value = ("No counter sources (traffic-counter unreachable or empty). "
                            "You can still place counters with manual volumes.")
    except Exception as e:  # noqa: BLE001
        status.value = f"Could not load sources: {e}"


def _place_counter(lat: float, lon: float) -> None:
    sid = scenario_id.value
    if not sid:
        status.value = "Create or load a scenario first."
        return
    src = _source_by_label.get(selected_source_label.value, {})
    counter_seq.value += 1
    try:
        res = api.create_counter(
            sid, name=f"C{counter_seq.value:02d}", lat=lat, lon=lon,
            source_video_id=src.get("video_id"), source_line_id=src.get("line_id"),
            link_direction=direction.value)
        if src.get("video_id"):
            try:
                api.pull_observations(sid, res["id"])
            except Exception as e:  # noqa: BLE001
                status.value = f"Counter placed; observation pull failed: {e}"
        counters_fc.value = api.get_counters(sid)
        status.value = f"Placed counter at {lat:.4f}, {lon:.4f}."
    except Exception as e:  # noqa: BLE001
        status.value = f"Could not place counter: {e}"


def on_run() -> None:
    sid = scenario_id.value
    try:
        m = api.run_4step(sid, beta=beta.value)
        flows_fc.value = api.link_flows(sid)
        counters_fc.value = api.get_counters(sid)
        _show_metrics("4-step (uncalibrated)", m)
        status.value = "4-step model run. Links coloured by GEH at counters — calibrate to improve the fit."
    except Exception as e:  # noqa: BLE001
        status.value = f"Run failed: {e}"


def on_calibrate() -> None:
    sid = scenario_id.value
    try:
        res = api.calibrate(sid)
        flows_fc.value = api.link_flows(sid)
        _show_metrics("Calibrated (ODME)", res["after"])
        b, a = res["before"], res["after"]
        status.value = (f"Calibrated: mean GEH {b['mean_geh']:.2f} → {a['mean_geh']:.2f}, "
                        f"GEH&lt;5 {b['pct_geh_lt5']:.0f}% → {a['pct_geh_lt5']:.0f}%.")
    except Exception as e:  # noqa: BLE001
        status.value = f"Calibration failed: {e}"


# --- page ------------------------------------------------------------------ #
@solara.component
def Page():
    solara.Title("MacroModel")

    # Build the layer element list from reactive data (rebuilt on every change).
    overlays = [layers.tile_layer_element()]
    if network_fc.value:
        overlays += layers.network_elements(network_fc.value)
    if flows_fc.value:
        overlays += layers.flows_elements(flows_fc.value)
    if zones_fc.value:
        overlays += layers.zone_elements(zones_fc.value)
    if counters_fc.value:
        overlays += layers.counter_elements(counters_fc.value)

    with solara.Sidebar():
        solara.Markdown("## 🚦 MacroModel\n*counts → georeference → 4-step → calibrate*")
        with solara.Card("1 · Scenario"):
            solara.Button("Load demo scenario", on_click=on_load_demo, color="primary")
            solara.InputText("New scenario name", value=new_name)
            solara.Button("Create + sample network", on_click=on_create_sample)
            solara.Markdown(f"**Active:** `{(scenario_id.value or '—')[:8]}`")
        with solara.Card("2 · Zones"):
            solara.Button("Auto-generate zones", on_click=on_auto_zones)
        with solara.Card("3 · Counters (georeference)"):
            solara.Button("Load counter sources", on_click=on_load_sources)
            if source_labels.value:
                solara.Select("Counting line", value=selected_source_label, values=source_labels.value)
            solara.Select("Direction (line +ve →)", value=direction, values=["AB", "BA"])
            solara.Switch(label="Click map to place counter", value=place_mode)
        with solara.Card("4 · Run 4-step"):
            solara.SliderFloat("Gravity β", value=beta, min=0.02, max=0.4, step=0.01)
            solara.Button("Run 4-step", on_click=on_run, color="primary")
        with solara.Card("5 · Calibrate"):
            solara.Button("Calibrate (ODME)", on_click=on_calibrate, color="primary")
        with solara.Card("Status"):
            solara.Markdown(status.value)
            if metrics_md.value:
                solara.Markdown(metrics_md.value)
        solara.Markdown("Legend — GEH: 🟩 &lt;5 · 🟧 5–10 · 🟥 &gt;10")

    with solara.Column(style={"height": "92vh"}):
        map_el = L.Map.element(
            center=center.value, zoom=zoom.value, scroll_wheel_zoom=True,
            layout=W.Layout(height="100%", width="100%"), layers=overlays)

    def _attach_click():
        widget = get_widget(map_el)

        def handler(**kw):
            if kw.get("type") == "click" and place_mode.value:
                coords = kw.get("coordinates") or (None, None)
                if coords[0] is not None:
                    _place_counter(coords[0], coords[1])

        widget.on_interaction(handler)
        return lambda: widget.on_interaction(handler, remove=True)

    solara.use_effect(_attach_click, [])
