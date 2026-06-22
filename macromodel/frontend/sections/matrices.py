"""Matrix section: list demand/skim matrices, view values as a labelled grid, edit
cells, scale, and create blank matrices.
"""
from __future__ import annotations

import pandas as pd
import solara

import api_client as api
import state

_refresh = solara.reactive(0)
_ci = solara.reactive("0")
_cj = solara.reactive("0")
_cv = solara.reactive("0")
_factor = solara.reactive("1.0")


def _bump():
    _refresh.value += 1


def _open(mid):
    state.selected_matrix.value = mid
    try:
        state.matrix_view.value = api.matrix_values(mid)
    except Exception as e:  # noqa: BLE001
        state.status.value = f"open failed: {e}"


@solara.component
def Section():
    sid = state.scenario_id.value
    mats = solara.use_memo(lambda: (api.list_matrices(sid) if sid else []), [sid, _refresh.value])
    labels = [f"{m['name']}  [{m.get('kind')}/{m.get('step')}]  {m['n_zones']}×{m['n_zones']}" for m in mats]
    by_label = {l: m for l, m in zip(labels, mats)}
    cur = next((l for l in labels if by_label[l]["id"] == state.selected_matrix.value), None)

    solara.Markdown("## Matrices")
    with solara.Row(style={"align-items": "center"}):
        if labels:
            solara.Select("Matrix", value=cur, values=labels, on_value=lambda l: _open(by_label[l]["id"]))
        solara.Button("+ Blank", on_click=lambda: _blank(sid))
        if state.selected_matrix.value:
            solara.Button("Delete", color="error", on_click=lambda: _del())

    v = state.matrix_view.value
    if v and state.selected_matrix.value:
        labels_z = v["labels"]
        df = pd.DataFrame(v["values"], columns=labels_z)
        df.insert(0, "O \\ D", labels_z)
        solara.Markdown(f"**{v['name']}** — total **{v['total']:.0f}**")
        solara.DataFrame(df, items_per_page=12)
        with solara.Row(style={"align-items": "center"}):
            solara.InputText("row i", value=_ci)
            solara.InputText("col j", value=_cj)
            solara.InputText("value", value=_cv)
            solara.Button("Set cell", on_click=lambda: _set_cell())
            solara.InputText("× factor", value=_factor)
            solara.Button("Scale", on_click=lambda: _scale())
    else:
        solara.Markdown("*Pick a matrix to view/edit. Run Procedures to generate demand/skim matrices.*")


def _set_cell():
    try:
        api.update_cell(state.selected_matrix.value, int(_ci.value), int(_cj.value), float(_cv.value))
        _open(state.selected_matrix.value)
        state.status.value = "Cell updated."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Set failed: {e}"


def _scale():
    try:
        api.scale_matrix(state.selected_matrix.value, float(_factor.value))
        _open(state.selected_matrix.value)
        state.status.value = "Matrix scaled."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Scale failed: {e}"


def _blank(sid):
    try:
        m = api.blank_matrix(sid, "New matrix")
        _bump()
        _open(m["id"])
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Blank failed: {e}"


def _del():
    try:
        api.delete_matrix(state.selected_matrix.value)
        state.selected_matrix.value = ""
        state.matrix_view.value = None
        _bump()
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Delete failed: {e}"
