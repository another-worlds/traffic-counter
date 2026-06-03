"""MacroModel — Solara + ipyleaflet front end.

A single map with a step sidebar mirroring the macroscopic pipeline:
scenario → network → zones → georeferenced counters → 4-step → calibration.
The map widget is a stable module-level object mutated imperatively by the action
callbacks; UI state lives in solara.reactive values.
"""
from __future__ import annotations

import ipyleaflet as L
import solara

import api_client as api
import layers

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

_source_by_label: dict = {}  # label -> {"video_id", "line_id"}

# --- stable map + imperative layer registry -------------------------------- #
_map = L.Map(center=(41.31, 69.27), zoom=13, scroll_wheel_zoom=True)
_map.layout.height = "96vh"
_layers: dict = {}


def _set_layer(key: str, layer) -> None:
    old = _layers.get(key)
    if old is not None:
        try:
            _map.remove_layer(old)
        except Exception:
            pass
    _layers[key] = layer
    if layer is not None:
        _map.add_layer(layer)


def _fit(fc: dict) -> None:
    b = layers.bounds_of(fc)
    if b:
        _map.fit_bounds(b)


def refresh_network_layers() -> None:
    sid = scenario_id.value
    if not sid:
        return
    net = api.get_network(sid)
    _set_layer("network", layers.network_layer(net))
    _set_layer("zones", layers.zone_layer(api.get_zones(sid)))
    _set_layer("counters", layers.counter_layer(api.get_counters(sid)))
    _set_layer("flows", None)
    _fit(net)


def refresh_flows() -> None:
    sid = scenario_id.value
    if not sid:
        return
    _set_layer("flows", layers.linkflow_layer(api.link_flows(sid)))
    _set_layer("counters", layers.counter_layer(api.get_counters(sid)))


def _on_map_click(**kwargs) -> None:
    if kwargs.get("type") != "click" or not place_mode.value:
        return
    coords = kwargs.get("coordinates") or (None, None)
    lat, lon = coords
    if lat is None:
        return
    src = _source_by_label.get(selected_source_label.value, {})
    counter_seq.value += 1
    try:
        res = api.create_counter(
            scenario_id.value, name=f"C{counter_seq.value:02d}", lat=lat, lon=lon,
            source_video_id=src.get("video_id"), source_line_id=src.get("line_id"),
            link_direction=direction.value,
        )
        if src.get("video_id"):
            try:
                api.pull_observations(scenario_id.value, res["id"])
            except Exception as e:  # noqa: BLE001
                status.value = f"Counter placed; observation pull failed: {e}"
        _set_layer("counters", layers.counter_layer(api.get_counters(scenario_id.value)))
        status.value = f"Placed counter at {lat:.4f}, {lon:.4f}."
    except Exception as e:  # noqa: BLE001
        status.value = f"Could not place counter: {e}"


_map.on_interaction(_on_map_click)


# --- actions --------------------------------------------------------------- #
def _show_metrics(title: str, m: dict) -> None:
    metrics_md.value = (
        f"**{title}**\n\n"
        f"- mean GEH: **{(m.get('mean_geh') or 0):.2f}**\n"
        f"- GEH &lt; 5: **{(m.get('pct_geh_lt5') or 0):.0f}%**\n"
        f"- RMSE: **{(m.get('rmse') or 0):.0f}** vph\n"
    )


def on_load_demo() -> None:
    try:
        sc = api.create_demo()
        scenario_id.value = sc["id"]
        refresh_network_layers()
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
        refresh_network_layers()
        status.value = "Scenario created with sample network + 8 zones. Add counters or run the model."
    except Exception as e:  # noqa: BLE001
        status.value = f"Create failed: {e}"


def on_auto_zones() -> None:
    try:
        api.auto_zones(scenario_id.value, n=8)
        refresh_network_layers()
        status.value = "Auto-generated 8 zones."
    except Exception as e:  # noqa: BLE001
        status.value = f"Auto-zones failed: {e}"


def on_load_sources() -> None:
    try:
        data = api.counter_sources()
        labels, _source_by_label_local = [], {}
        for proj in data.get("projects", []):
            for v in proj.get("videos", []):
                for ln in v.get("lines", []):
                    lbl = f"{proj.get('name')} / {v.get('filename')} / {ln.get('name')}"
                    labels.append(lbl)
                    _source_by_label_local[lbl] = {"video_id": v["video_id"], "line_id": ln["line_id"]}
        _source_by_label.clear()
        _source_by_label.update(_source_by_label_local)
        source_labels.value = labels
        if labels:
            selected_source_label.value = labels[0]
            status.value = f"Loaded {len(labels)} counting lines from the traffic-counter."
        else:
            status.value = ("No counter sources (traffic-counter unreachable or empty). "
                            "You can still place counters with manual volumes.")
    except Exception as e:  # noqa: BLE001
        status.value = f"Could not load sources: {e}"


def on_run() -> None:
    try:
        m = api.run_4step(scenario_id.value, beta=beta.value)
        refresh_flows()
        _show_metrics("4-step (uncalibrated)", m)
        status.value = "4-step model run. Links coloured by GEH at counters — calibrate to improve the fit."
    except Exception as e:  # noqa: BLE001
        status.value = f"Run failed: {e}"


def on_calibrate() -> None:
    try:
        res = api.calibrate(scenario_id.value)
        refresh_flows()
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

    solara.display(_map)
