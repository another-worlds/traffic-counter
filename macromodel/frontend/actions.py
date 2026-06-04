"""Data-loading actions shared across sections (API calls → reactive state)."""
from __future__ import annotations

import api_client as api
import layers
import state


def refresh_scenarios() -> None:
    try:
        state.scenarios.value = api.list_scenarios()
    except Exception as e:  # noqa: BLE001
        state.status.value = f"API error: {e}"


def ensure_default() -> None:
    """On startup the base scenario is always an empty, editable one (demo is opt-in)."""
    refresh_scenarios()
    if state.scenario_id.value:
        return
    empty = next((s for s in state.scenarios.value if (s.get("n_nodes") or 0) == 0), None)
    try:
        if empty:
            select_scenario(empty["id"])
        else:
            sc = api.create_scenario("New scenario")
            refresh_scenarios()
            select_scenario(sc["id"])
        state.status.value = "Empty scenario ready — build a network, or Load demo (Sioux Falls)."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"API error: {e}"


def refresh_map() -> None:
    sid = state.scenario_id.value
    if not sid:
        return
    try:
        state.map_data.value = api.get_map(sid)
        state.flows_fc.value = None
        state.data_version.value += 1
    except Exception as e:  # noqa: BLE001
        state.status.value = f"map load failed: {e}"


def refresh_flows() -> None:
    sid = state.scenario_id.value
    if not sid:
        return
    try:
        state.flows_fc.value = api.link_flows(sid)
    except Exception as e:  # noqa: BLE001
        state.status.value = f"flows load failed: {e}"


def recenter() -> None:
    md = state.map_data.value
    if not md:
        return
    b = layers.bounds_of(md.get("nodes") or {})
    if b:
        (s, w), (n, e) = b
        state.map_center.value = ((s + n) / 2.0, (w + e) / 2.0)


def select_scenario(sid: str) -> None:
    state.scenario_id.value = sid
    state.selected.value = None
    refresh_map()
    recenter()


def load_demo() -> None:
    state.busy.value = True
    try:
        sc = api.create_demo()
        refresh_scenarios()
        select_scenario(sc["id"])
        state.status.value = (f"Demo loaded: {sc['n_nodes']} nodes, {sc['n_links']} links, "
                              f"{sc['n_zones']} zones, {sc['n_counters']} detectors.")
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Demo failed: {e}"
    finally:
        state.busy.value = False


def create_scenario(name: str) -> None:
    state.busy.value = True
    try:
        sc = api.create_scenario(name)
        api.load_sample(sc["id"])
        api.auto_zones(sc["id"], 8)
        refresh_scenarios()
        select_scenario(sc["id"])
        state.status.value = "New scenario with sample network + 8 zones."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Create failed: {e}"
    finally:
        state.busy.value = False


def create_blank_scenario(name: str = "New scenario") -> None:
    """An empty scenario (default classes only) — build a network from zero."""
    try:
        sc = api.create_scenario(name)
        refresh_scenarios()
        select_scenario(sc["id"])
        state.status.value = "Blank scenario ready — draw a network, search a place, or import OSM."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Create failed: {e}"


def delete_current_scenario() -> None:
    sid = state.scenario_id.value
    if not sid:
        return
    try:
        api.delete_scenario(sid)
        state.scenario_id.value = ""
        refresh_scenarios()
        nxt = state.scenarios.value[0]["id"] if state.scenarios.value else ""
        if nxt:
            select_scenario(nxt)
        else:
            state.map_data.value = None
        state.status.value = "Scenario deleted."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Delete failed: {e}"
