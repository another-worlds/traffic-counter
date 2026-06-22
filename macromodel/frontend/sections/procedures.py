"""Procedures section: the ordered 4-step calculation sequence (Visum-style function
editor). Add / reorder / toggle / run operations and view the run log + KPIs.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import solara

import actions
import api_client as api
import state


def _style_dark(ax):
    ax.set_facecolor("#0f141c")
    for s in ax.spines.values():
        s.set_color("#33405a")
    ax.tick_params(colors="#9fb3d4", labelsize=8)
    ax.xaxis.label.set_color("#9fb3d4")
    ax.yaxis.label.set_color("#9fb3d4")
    ax.title.set_color("#dbe6f7")


@solara.component
def ValidationChart():
    flows = state.flows_fc.value
    pts = [] if not flows else [
        (f["properties"]["obs_vph"], f["properties"]["sim_vph"], f["properties"].get("geh") or 0.0)
        for f in flows["features"] if f["properties"].get("obs_vph") is not None]

    def make_fig():
        if not pts:
            return None
        obs = [p[0] for p in pts]; sim = [p[1] for p in pts]; geh = [p[2] for p in pts]
        cols = ["#2ca25f" if g < 5 else "#f4a300" if g < 10 else "#d7301f" for g in geh]
        fig, ax = plt.subplots(1, 2, figsize=(8.2, 3.1))
        fig.patch.set_facecolor("#1b212c")
        mx = max(max(obs), max(sim)) * 1.1 or 1
        ax[0].plot([0, mx], [0, mx], "--", color="#566", lw=1)
        ax[0].scatter(obs, sim, c=cols, s=34, edgecolor="k", linewidth=0.3)
        ax[0].set_xlabel("observed vph"); ax[0].set_ylabel("modelled vph")
        ax[0].set_title("Observed vs modelled")
        ax[1].hist(geh, bins=[0, 2, 4, 5, 6, 8, 10, 15], color="#3aa0ff", edgecolor="#11161e")
        ax[1].axvline(5, color="#2ca25f", ls="--", lw=1.2)
        ax[1].set_xlabel("GEH"); ax[1].set_title("GEH distribution")
        _style_dark(ax[0]); _style_dark(ax[1])
        fig.tight_layout()
        return fig

    fig = solara.use_memo(make_fig, [tuple(pts)])  # called unconditionally (rules of hooks)
    if fig is not None:
        solara.FigureMatplotlib(fig)

_refresh = solara.reactive(0)
_new_op = solara.reactive("TripGeneration")


def _bump():
    _refresh.value += 1


def _add(sid):
    try:
        api.add_procedure(sid, _new_op.value)
        _bump()
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Add failed: {e}"


def _toggle(pid, v):
    api.update_procedure(pid, {"active": v})
    _bump()


def _delete(pid):
    api.delete_procedure(pid)
    _bump()


def _move(sid, procs, idx, delta):
    j = idx + delta
    if j < 0 or j >= len(procs):
        return
    ids = [p["id"] for p in procs]
    ids[idx], ids[j] = ids[j], ids[idx]
    api.reorder_procedures(sid, ids)
    _bump()


def _apply_results(res):
    state.proc_log.value = res.get("results", [])
    state.proc_metrics.value = res.get("metrics")
    actions.refresh_map()    # refresh geometry first (this clears stale flows) …
    actions.refresh_flows()  # … then load the fresh assigned/calibrated link flows
    _bump()


def _run_all(sid):
    try:
        state.status.value = "Running procedures (UXsim assignment + ODME)…"
        _apply_results(api.run_procedures(sid))
        state.status.value = "Procedure sequence finished."
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Run failed: {e}"


def _run_one(pid):
    try:
        _apply_results(api.run_one_procedure(pid))
    except Exception as e:  # noqa: BLE001
        state.status.value = f"Run failed: {e}"


@solara.component
def Section():
    sid = state.scenario_id.value
    procs = solara.use_memo(lambda: (api.list_procedures(sid) if sid else []), [sid, _refresh.value])
    ops = solara.use_memo(api.op_types, [])

    solara.Markdown("## Procedures — 4-step calculation sequence")
    with solara.Row(style={"align-items": "center"}):
        solara.Select("Add operation", value=_new_op, values=ops or [_new_op.value])
        solara.Button("+ Add", on_click=lambda: _add(sid))
        solara.Button("▶ Run all", color="primary", on_click=lambda: _run_all(sid))

    if not procs:
        solara.Markdown("*No procedures — load a scenario (the default 4-step sequence is seeded).*")
    for i, p in enumerate(procs):
        with solara.Card():
            with solara.Row(style={"align-items": "center"}):
                solara.Switch(value=bool(p["active"]), on_value=lambda v, pid=p["id"]: _toggle(pid, v))
                solara.Markdown(f"**{i + 1}. {p['op_type']}** — {p.get('name', '')}")
                solara.Button("↑", text=True, on_click=lambda idx=i: _move(sid, procs, idx, -1))
                solara.Button("↓", text=True, on_click=lambda idx=i: _move(sid, procs, idx, 1))
                solara.Button("Run", text=True, on_click=lambda pid=p["id"]: _run_one(pid))
                solara.Button("✕", text=True, on_click=lambda pid=p["id"]: _delete(pid))
            if p.get("last_status"):
                solara.Markdown(f"`{p['last_status']}` — {p.get('last_log') or ''}")

    if state.proc_metrics.value:
        m = state.proc_metrics.value
        solara.Markdown(
            f"### Result\nmean GEH **{(m.get('mean_geh') or 0):.2f}** · "
            f"GEH&lt;5 **{(m.get('pct_geh_lt5') or 0):.0f}%** · RMSE **{(m.get('rmse') or 0):.0f}** vph "
            "— see coloured links in the Network tab.")
    ValidationChart()
