"""Lists section: tabular views of every object type with a row editor. Doubles as
the class editors (link/node/zone types) and demand-object editors.
"""
from __future__ import annotations

import pandas as pd
import solara

import api_client as api
import state

OBJECTS = ["links", "nodes", "zones", "connectors", "stops", "lines", "detectors",
           "link_types", "node_types", "zone_types", "modes", "activities",
           "demand_layers", "zone_demand", "mode_choice_params"]

FIELDS = {
    "links": ["name", "link_type_id", "lanes", "free_flow_speed_ms", "v0_kmh", "capacity_vph", "oneway"],
    "nodes": ["name", "node_type_id"],
    "zones": ["name", "zone_type_id", "production", "attraction"],
    "connectors": ["zone_id", "node_id", "direction", "t0_min", "weight"],
    "stops": ["name", "node_id"],
    "lines": ["name", "tsys", "headway_min", "color"],
    "detectors": ["name", "link_direction", "observed_vph", "pcu_vph"],
    "link_types": ["name", "rank", "num_lanes", "capacity_vph", "v0_kmh"],
    "node_types": ["name", "control"],
    "zone_types": ["name", "category"],
    "modes": ["code", "name", "is_prt", "assignment"],
    "activities": ["code", "name", "is_home"],
    "demand_layers": ["code", "name", "from_activity", "to_activity", "beta"],
    "zone_demand": ["zone_id", "activity", "production", "attraction"],
    "mode_choice_params": ["demand_layer", "mode_code", "asc", "beta_time"],
}
GEOMETRIC = {"nodes", "links", "zones", "stops", "detectors"}

_refresh = solara.reactive(0)
_edit_id = solara.reactive("")
_buf = solara.reactive({})


def _bump():
    _refresh.value += 1


def _coerce(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, bool) or v is None:
            out[k] = v
        elif isinstance(v, str):
            s = v.strip()
            if s.lower() in ("true", "false"):
                out[k] = s.lower() == "true"
            else:
                try:
                    out[k] = float(s) if ("." in s or "e" in s.lower()) else int(s)
                except ValueError:
                    out[k] = s
        else:
            out[k] = v
    return out


def _load(obj, row):
    _edit_id.value = row["id"]
    _buf.value = {f: row.get(f) for f in FIELDS.get(obj, [])}


@solara.component
def RowEditor(obj, rows):
    solara.Markdown("### Edit")
    labels = [f"{r.get('name') or r.get('code') or r['id'][:6]} · {r['id'][:6]}" for r in rows]
    by_label = {l: r for l, r in zip(labels, rows)}
    cur = next((l for l in labels if by_label[l]["id"] == _edit_id.value), None)
    if labels:
        solara.Select("Row", value=cur, values=labels, on_value=lambda l: _load(obj, by_label[l]))

    if _edit_id.value:
        for f in FIELDS.get(obj, []):
            v = _buf.value.get(f)
            if isinstance(v, bool):
                solara.Switch(label=f, value=v, on_value=lambda nv, f=f: _buf.set({**_buf.value, f: nv}))
            else:
                solara.InputText(f, value="" if v is None else str(v),
                                 on_value=lambda nv, f=f: _buf.set({**_buf.value, f: nv}))

        def save():
            try:
                api.update_object(obj, _edit_id.value, _coerce(_buf.value))
                state.status.value = "Saved."
                _bump()
            except Exception as e:  # noqa: BLE001
                state.status.value = f"Save failed: {e}"

        def delete():
            try:
                api.delete_object(obj, _edit_id.value)
                _edit_id.value = ""
                state.status.value = "Deleted."
                _bump()
            except Exception as e:  # noqa: BLE001
                state.status.value = f"Delete failed: {e}"

        with solara.Row():
            solara.Button("Save", on_click=save, color="primary")
            solara.Button("Delete", on_click=delete, color="error")

    if obj not in GEOMETRIC:
        def add():
            try:
                row = api.create_object(state.scenario_id.value, obj, {})
                _load(obj, row)
                _bump()
                state.status.value = "Added blank row — edit and Save."
            except Exception as e:  # noqa: BLE001
                state.status.value = f"Add failed: {e}"

        solara.Button("+ Add row", on_click=add)
    else:
        solara.Markdown("*Create geometric objects in the Network tab.*")


@solara.component
def Section():
    sid = state.scenario_id.value
    obj = state.list_obj.value
    rows = solara.use_memo(
        lambda: (api.list_objects(sid, obj) if sid else []), [sid, obj, _refresh.value])
    with solara.Row():
        with solara.Column(style={"min-width": "180px", "max-width": "200px"}):
            solara.Markdown("**Object type**")
            solara.Select("Type", value=state.list_obj, values=OBJECTS)
            solara.Markdown(f"{len(rows)} rows")
        with solara.Column(style={"flex": "1"}):
            if rows:
                df = pd.DataFrame(rows)
                cols = [c for c in (["id"] + FIELDS.get(obj, [])) if c in df.columns]
                solara.DataFrame(df[cols], items_per_page=15)
            else:
                solara.Markdown("*No rows — pick a scenario, or add one on the right.*")
        with solara.Column(style={"min-width": "260px", "max-width": "300px"}):
            RowEditor(obj, rows)
