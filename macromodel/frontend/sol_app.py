"""MacroModel — a lightweight, Visum-style macroscopic modelling app (Solara).

App shell: a scenario selector + section tabs (Network · Lists · Demand · Procedures ·
Matrices). Each section is its own component; shared reactive state lives in state.py.
Widgets are created inside components (never at import) per Solara's rules.
"""
from __future__ import annotations

import solara

import actions
import api_client as api
import state
import theme
from sections import demand, lists, matrices, network, procedures


def _enable_dark():
    try:
        import solara.lab
        try:
            solara.lab.theme.dark.value = True
        except Exception:
            solara.lab.theme.dark = True
    except Exception:
        pass

SECTIONS = {
    "Network": network.Section,
    "Lists": lists.Section,
    "Demand": demand.Section,
    "Procedures": procedures.Section,
    "Matrices": matrices.Section,
}


_confirm_del = solara.reactive(False)
_renaming = solara.reactive(False)
_rename_buf = solara.reactive("")


def _do_rename():
    try:
        api.rename_scenario(state.scenario_id.value, _rename_buf.value)
        actions.refresh_scenarios()
        state.status.value = "Scenario renamed."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Rename failed: {e}"
    _renaming.value = False


def _do_delete():
    _confirm_del.value = False
    actions.delete_current_scenario()


@solara.component
def ScenarioBar():
    scs = state.scenarios.value
    labels = [f"{s['name']} · {s['id'][:6]}" for s in scs]
    by_label = {l: s["id"] for l, s in zip(labels, scs)}
    cur = next((l for l in labels if by_label[l] == state.scenario_id.value), None)
    if _renaming.value and state.scenario_id.value:
        solara.InputText("New name", value=_rename_buf.value, on_value=_rename_buf.set)
        solara.Button("Save", text=True, on_click=_do_rename)
        solara.Button("Cancel", text=True, on_click=lambda: _renaming.set(False))
        return
    if labels:
        solara.Select("Scenario", value=cur, values=labels,
                      on_value=lambda l: actions.select_scenario(by_label[l]))
    solara.Button("New", icon_name="mdi-plus", text=True, on_click=lambda: actions.create_blank_scenario())
    solara.Button("Load demo (Sioux Falls)", color="primary", on_click=actions.load_demo)
    if state.scenario_id.value:
        cur_name = next((s["name"] for s in scs if s["id"] == state.scenario_id.value), "")
        solara.Button("Rename", text=True, on_click=lambda: (_rename_buf.set(cur_name), _renaming.set(True)))
        if _confirm_del.value:
            solara.Button("Confirm delete", color="error", on_click=_do_delete)
            solara.Button("Cancel", text=True, on_click=lambda: _confirm_del.set(False))
        else:
            solara.Button("Delete", text=True, on_click=lambda: _confirm_del.set(True))


@solara.component
def Page():
    solara.Title("MacroModel")
    solara.Style(theme.CSS)
    solara.use_effect(_enable_dark, [])
    solara.use_effect(actions.ensure_default, [])

    with solara.Column(classes=["mm-appbar"]):
        with solara.Row(style={"align-items": "center", "gap": "16px"}):
            solara.HTML(tag="div", unsafe_innerHTML="🚦 <b class='mm-title'>MacroModel</b>")
            ScenarioBar()
            solara.ToggleButtonsSingle(value=state.section, values=list(SECTIONS))
        solara.Markdown(state.status.value)

    with solara.Column(style={"padding": "0 6px"}):
        SECTIONS[state.section.value]()
