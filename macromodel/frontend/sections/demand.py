"""Demand section: modes, activities, demand layers, mode-choice params, and per-zone
per-activity production/attraction — the inputs to the 4-step demand calculation.
Reuses the generic table + row editor from the Lists section.
"""
from __future__ import annotations

import pandas as pd
import solara

import api_client as api
import state
from . import lists

DEMAND_OBJS = ["demand_layers", "modes", "activities", "mode_choice_params", "zone_demand"]
_demand_obj = solara.reactive("demand_layers")


@solara.component
def Section():
    sid = state.scenario_id.value
    obj = _demand_obj.value
    rows = solara.use_memo(
        lambda: (api.list_objects(sid, obj) if sid else []),
        [sid, obj, lists._refresh.value])

    solara.Markdown("## Demand model & modes")
    solara.Markdown(
        "Modes **PrT / PuT** · activities **Home / Work / Other** · demand layers "
        "**HW, WH, HO, OH** · per-zone-per-activity production/attraction · binary-logit "
        "mode-choice params. Edit on the right; these feed Trip generation → distribution → mode choice.")
    with solara.Row():
        with solara.Column(style={"min-width": "190px", "max-width": "210px"}):
            solara.Select("Demand object", value=_demand_obj, values=DEMAND_OBJS)
            solara.Markdown(f"{len(rows)} rows")
        with solara.Column(style={"flex": "1"}):
            if rows:
                df = pd.DataFrame(rows)
                cols = [c for c in (["id"] + lists.FIELDS.get(obj, [])) if c in df.columns]
                solara.DataFrame(df[cols], items_per_page=15)
            else:
                solara.Markdown("*No rows — load or create a scenario.*")
        with solara.Column(style={"min-width": "260px", "max-width": "300px"}):
            lists.RowEditor(obj, rows)
