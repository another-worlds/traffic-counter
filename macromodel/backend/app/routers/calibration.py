from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..helpers import latest_distributed_od, store_assignment, store_od
from ..models import CalibrationRun
from ..schemas import CalibrateRequest
from ..services import calibration as calib
from ..services import loader, netconvert, pipeline
from ..storage import storage
from .model import _zones_or_400
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["calibration"])


@router.post("/scenarios/{scenario_id}/calibrate")
def calibrate(scenario_id: str, body: CalibrateRequest, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, links = loader.load_network(db, scenario_id)
    zones = _zones_or_400(db, scenario_id)
    connectors = [z["connector_node_id"] for z in zones]

    targets = loader.load_counter_targets(db, scenario_id)
    if not targets:
        raise HTTPException(400, "no georeferenced counters with observed volumes to calibrate against")

    od = latest_distributed_od(db, scenario_id)
    if od:
        T0 = storage.load_matrix(od.storage_ref)
    else:
        T0, _, _ = pipeline.seed_od(nodes, links, zones, beta=0.1)

    G = netconvert.build_graph(nodes, links)
    assign_fn = pipeline.make_assign_fn(nodes, links, connectors, settings.uxsim_deltan, settings.sim_duration_s)
    max_iters = body.max_iters or settings.max_calibration_iters

    T_cal, history = calib.calibrate(
        T0, connectors,
        [{"link_id": t["link_id"], "target_vph": t["target_vph"]} for t in targets],
        assign_fn, G, max_iters=max_iters,
        clamp=settings.odme_factor_clamp, damping=settings.odme_damping,
    )

    m = store_od(db, scenario_id, T_cal, step="calibrated", name="ODME calibrated")
    sim_final = assign_fn(T_cal)
    res, _ = store_assignment(db, scenario_id, m.id, sim_final, links, targets)

    before, after = history[0], history[-1]
    run = CalibrationRun(
        scenario_id=scenario_id, method="path_multiplicative_odme", iterations=max_iters,
        od_matrix_id=m.id, mean_geh=after["mean_geh"], pct_geh_lt5=after["pct_geh_lt5"],
        converged=after["pct_geh_lt5"] >= 85.0, history=history,
    )
    db.add(run)
    db.commit()
    return {
        "calibration_run_id": run.id, "od_matrix_id": m.id, "assignment_id": res.id,
        "before": before, "after": after, "converged": run.converged, "history": history,
    }


@router.get("/scenarios/{scenario_id}/calibration/{run_id}")
def get_run(scenario_id: str, run_id: str, db: Session = Depends(get_db)):
    run = db.query(CalibrationRun).filter(CalibrationRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "calibration run not found")
    return {
        "id": run.id, "method": run.method, "iterations": run.iterations,
        "mean_geh": run.mean_geh, "pct_geh_lt5": run.pct_geh_lt5,
        "converged": run.converged, "history": run.history, "od_matrix_id": run.od_matrix_id,
    }
