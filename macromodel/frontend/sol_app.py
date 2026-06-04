"""MacroModel — a lightweight, Visum-style macroscopic modelling app (Solara).

App shell: a scenario selector + section tabs (Network · Lists · Demand · Procedures ·
Matrices). Each section is its own component; shared reactive state lives in state.py.
Widgets are created inside components (never at import) per Solara's rules.
"""
from __future__ import annotations

import solara

import actions
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


@solara.component
def ScenarioBar():
    labels = [f"{s['name']} · {s['id'][:6]}" for s in state.scenarios.value]
    by_label = {l: s["id"] for l, s in zip(labels, state.scenarios.value)}
    cur = next((l for l in labels if by_label[l] == state.scenario_id.value), None)
    if labels:
        solara.Select("Scenario", value=cur, values=labels,
                      on_value=lambda l: actions.select_scenario(by_label[l]))
    solara.Button("Load demo (Sioux Falls)", color="primary", on_click=actions.load_demo)


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
