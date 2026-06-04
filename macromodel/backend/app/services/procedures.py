"""The procedure-sequence executor (the 4-step "function editor" engine).

Each Procedure row is an operation with params; this runs the ordered, active ops,
threading a RunContext that holds the network, demand-layer matrices and the latest
assignment. Operations dispatch to the existing engines (generation, distribution,
modechoice, assignment, calibration). Handlers lazily resolve their prerequisites, so
"run one op" still works from a clean state.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

import numpy as np
from sqlalchemy.orm import Session

from ..config import settings
from ..helpers import store_assignment, store_od
from ..models import DemandLayer, LinkType, ModeChoiceParam, Procedure, ZoneDemand
from . import (
    assignment, calibration, distribution, generation, loader, modechoice, netconvert, pipeline,
)

OPS = ["SetAttribute", "TripGeneration", "TripDistribution", "ModeChoice",
       "PrTAssignment", "CalcSkim", "MatrixCorrection", "Validation"]

_DEFAULT_MC = {"PrT": {"asc": 0.0, "beta_time": -0.06}, "PuT": {"asc": -0.8, "beta_time": -0.04}}


class RunContext:
    def __init__(self, db: Session, scenario_id: str):
        self.db = db
        self.scenario_id = scenario_id
        self.nodes, self.links = loader.load_network(db, scenario_id)
        self.zones = loader.load_zones(db, scenario_id)
        self.connectors = [z["connector_node_id"] for z in self.zones]
        if not self.zones:
            raise ValueError("no zones defined")
        if any(c is None for c in self.connectors):
            raise ValueError("some zones have no connector node")
        self.G = netconvert.build_graph(self.nodes, self.links)
        self.targets = loader.load_counter_targets(db, scenario_id)
        self._skim = None
        self.gen: Dict[str, tuple] = {}
        self.demand_person: Dict[str, np.ndarray] = {}
        self.prt: Dict[str, np.ndarray] = {}
        self.prt_total: Optional[np.ndarray] = None
        self.last_sim: Optional[dict] = None

    def skim(self) -> np.ndarray:
        if self._skim is None:
            self._skim = distribution.skim_minutes(self.G, self.connectors)
        return self._skim


def _zone_demand(ctx: RunContext) -> Dict[str, Dict[str, tuple]]:
    m: Dict[str, Dict[str, tuple]] = defaultdict(dict)
    for zd in ctx.db.query(ZoneDemand).filter(ZoneDemand.scenario_id == ctx.scenario_id):
        m[zd.activity][zd.zone_id] = (zd.production or 0.0, zd.attraction or 0.0)
    return m


def _layers(ctx: RunContext) -> List[DemandLayer]:
    return ctx.db.query(DemandLayer).filter(DemandLayer.scenario_id == ctx.scenario_id).all()


def _mc_params(ctx: RunContext) -> Dict[str, Dict[str, dict]]:
    out: Dict[str, Dict[str, dict]] = defaultdict(dict)
    for p in ctx.db.query(ModeChoiceParam).filter(ModeChoiceParam.scenario_id == ctx.scenario_id):
        out[p.demand_layer][p.mode_code] = {"asc": p.asc, "beta_time": p.beta_time}
    return out


# --- operations ------------------------------------------------------------ #
def op_set_attribute(ctx, params):
    if params.get("action", "apply_link_types") == "apply_link_types":
        types = {t.id: t for t in ctx.db.query(LinkType).filter(LinkType.scenario_id == ctx.scenario_id)}
        from ..models import Link
        n = 0
        for l in ctx.db.query(Link).filter(Link.scenario_id == ctx.scenario_id):
            t = types.get(l.link_type_id)
            if t:
                l.capacity_vph = t.capacity_vph * (l.lanes or t.num_lanes)
                if not l.v0_kmh:
                    l.v0_kmh = t.v0_kmh
                n += 1
        ctx.db.flush()
        return f"applied link types to {n} links"
    return "no-op"


def op_generation(ctx, params):
    layers = _layers(ctx)
    zd = _zone_demand(ctx)
    zone_ids = [z["id"] for z in ctx.zones]
    if not zd:  # fallback to legacy per-zone production/attraction (single HW-like layer)
        P = np.array([z.get("production") or 0.0 for z in ctx.zones], float)
        A = np.array([z.get("attraction") or 0.0 for z in ctx.zones], float)
        code = layers[0].code if layers else "HW"
        ctx.gen = {code: generation.balance(P, A)}
        return f"generation (legacy zone P/A) → layer {code}, ΣP={P.sum():.0f}"
    ctx.gen = {}
    for layer in layers:
        P = np.array([zd.get(layer.from_activity, {}).get(z, (0.0, 0.0))[0] for z in zone_ids], float)
        A = np.array([zd.get(layer.to_activity, {}).get(z, (0.0, 0.0))[1] for z in zone_ids], float)
        ctx.gen[layer.code] = generation.balance(P, A)
    total = sum(P.sum() for P, _ in ctx.gen.values())
    return f"generation → {len(ctx.gen)} layers, ΣP={total:.0f}"


def op_distribution(ctx, params):
    if not ctx.gen:
        op_generation(ctx, {})
    skim = ctx.skim()
    layers = {l.code: l for l in _layers(ctx)}
    ctx.demand_person = {}
    for code, (P, A) in ctx.gen.items():
        beta = layers[code].beta if code in layers else params.get("beta", 0.1)
        T = distribution.gravity(P, A, skim, beta)
        ctx.demand_person[code] = T
        store_od(ctx.db, ctx.scenario_id, T, step="distributed", name=f"{code} person trips",
                 kind="demand", demand_layer=code)
    return f"distribution → {len(ctx.demand_person)} layer matrices"


def op_modechoice(ctx, params):
    if not ctx.demand_person:
        op_distribution(ctx, {})
    skim = ctx.skim()
    t_put = skim * modechoice.PUT_TIME_FACTOR
    mcp = _mc_params(ctx)
    ctx.prt = {}
    ctx.prt_total = None
    for code, T in ctx.demand_person.items():
        p = {**_DEFAULT_MC, **mcp.get(code, {})}
        p = {"PrT": p.get("PrT", _DEFAULT_MC["PrT"]), "PuT": p.get("PuT", _DEFAULT_MC["PuT"])}
        T_prt, T_put = modechoice.logit_split(T, skim, t_put, p)
        ctx.prt[code] = T_prt
        ctx.prt_total = T_prt if ctx.prt_total is None else ctx.prt_total + T_prt
        store_od(ctx.db, ctx.scenario_id, T_prt, step="distributed", name=f"{code} PrT",
                 kind="demand", mode="PrT", demand_layer=code)
        store_od(ctx.db, ctx.scenario_id, T_put, step="distributed", name=f"{code} PuT",
                 kind="demand", mode="PuT", demand_layer=code)
    store_od(ctx.db, ctx.scenario_id, ctx.prt_total, step="distributed", name="PrT total", mode="PrT")
    prt = ctx.prt_total.sum()
    return f"mode choice → PrT trips={prt:.0f}"


def op_prt_assignment(ctx, params):
    if ctx.prt_total is None:
        op_modechoice(ctx, {})
    sim, _ = assignment.assign(ctx.nodes, ctx.links, ctx.connectors, ctx.prt_total,
                               deltan=settings.uxsim_deltan, tmax=settings.sim_duration_s)
    ctx.last_sim = sim
    _, metrics = store_assignment(ctx.db, ctx.scenario_id, None, sim, ctx.links, ctx.targets)
    g = metrics.get("mean_geh")
    return f"PrT assignment done" + (f"; mean GEH={g:.2f}" if g is not None else "")


def op_calc_skim(ctx, params):
    store_od(ctx.db, ctx.scenario_id, ctx.skim(), step="skim", name="Skim: PrT time (min)",
             kind="skim", mode="PrT")
    return "computed PrT time skim"


def op_matrix_correction(ctx, params):
    if ctx.prt_total is None:
        op_modechoice(ctx, {})
    if not ctx.targets:
        return "no detectors with observed volumes — skipped"
    fn = pipeline.make_assign_fn(ctx.nodes, ctx.links, ctx.connectors,
                                 settings.uxsim_deltan, settings.sim_duration_s)
    counters = [{"link_id": t["link_id"], "target_vph": t["target_vph"]} for t in ctx.targets]
    T_cal, history = calibration.calibrate(
        ctx.prt_total, ctx.connectors, counters, fn, ctx.G,
        max_iters=int(params.get("max_iters", settings.max_calibration_iters)),
        clamp=settings.odme_factor_clamp, damping=settings.odme_damping)
    ctx.prt_total = T_cal
    ctx.last_sim = fn(T_cal)
    store_od(ctx.db, ctx.scenario_id, T_cal, step="calibrated", name="PrT calibrated", mode="PrT")
    store_assignment(ctx.db, ctx.scenario_id, None, ctx.last_sim, ctx.links, ctx.targets)
    a, b = history[-1], history[0]
    return (f"ODME: mean GEH {b['mean_geh']:.1f} → {a['mean_geh']:.1f}, "
            f"GEH<5 {b['pct_geh_lt5']:.0f}% → {a['pct_geh_lt5']:.0f}%")


def op_validation(ctx, params):
    if ctx.last_sim is None:
        op_prt_assignment(ctx, {})
    m = pipeline.evaluate(ctx.last_sim, ctx.targets)
    if m["mean_geh"] is None:
        return "validation: no detectors to compare against"
    return (f"validation: mean GEH={m['mean_geh']:.2f}, GEH<5={m['pct_geh_lt5']:.0f}%, "
            f"RMSE={m['rmse']:.0f} vph")


HANDLERS = {
    "SetAttribute": op_set_attribute,
    "TripGeneration": op_generation,
    "TripDistribution": op_distribution,
    "ModeChoice": op_modechoice,
    "PrTAssignment": op_prt_assignment,
    "CalcSkim": op_calc_skim,
    "MatrixCorrection": op_matrix_correction,
    "Validation": op_validation,
}


def run_sequence(db: Session, scenario_id: str, only_id: Optional[str] = None) -> dict:
    """Run the ordered active procedures (or a single op if only_id is given)."""
    ctx = RunContext(db, scenario_id)
    procs = (db.query(Procedure).filter(Procedure.scenario_id == scenario_id)
             .order_by(Procedure.idx).all())
    results = []
    for p in procs:
        if only_id and p.id != only_id:
            continue
        if not p.active:
            p.last_status = "skipped"
            results.append({"id": p.id, "op_type": p.op_type, "status": "skipped", "log": ""})
            continue
        handler = HANDLERS.get(p.op_type)
        try:
            msg = handler(ctx, p.params or {}) if handler else f"unknown op {p.op_type}"
            p.last_status, p.last_log = "ok", msg
            results.append({"id": p.id, "op_type": p.op_type, "status": "ok", "log": msg})
        except Exception as e:  # noqa: BLE001 — report and continue
            p.last_status, p.last_log = "error", str(e)
            results.append({"id": p.id, "op_type": p.op_type, "status": "error", "log": str(e)})
    db.commit()
    metrics = pipeline.evaluate(ctx.last_sim, ctx.targets) if ctx.last_sim else None
    return {"results": results, "metrics": metrics}
