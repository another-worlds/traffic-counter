from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..helpers import latest_distributed_od, store_assignment, store_od
from ..models import ODMatrix
from ..schemas import AssignmentRequest, DistributionRequest, RunFourStepRequest
from ..services import assignment, generation, loader, pipeline
from ..storage import storage
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["model"])


def _zones_or_400(db: Session, scenario_id: str):
    zones = loader.load_zones(db, scenario_id)
    if not zones:
        raise HTTPException(400, "define zones first")
    if any(z["connector_node_id"] is None for z in zones):
        raise HTTPException(400, "some zones have no connector node — (re)create zones after importing the network")
    return zones


def _assign(nodes, links, connectors, T):
    return assignment.assign(nodes, links, connectors, T,
                             deltan=settings.uxsim_deltan, tmax=settings.sim_duration_s)


@router.post("/scenarios/{scenario_id}/trip-generation")
def trip_generation(scenario_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    zones = _zones_or_400(db, scenario_id)
    P, A = generation.from_zones(zones)
    return {
        "zones": [{"id": z["id"], "name": z["name"], "production": float(p), "attraction": float(a)}
                  for z, p, a in zip(zones, P, A)],
        "total_productions": float(P.sum()), "total_attractions": float(A.sum()),
    }


@router.post("/scenarios/{scenario_id}/trip-distribution")
def trip_distribution(scenario_id: str, body: DistributionRequest, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, links = loader.load_network(db, scenario_id)
    zones = _zones_or_400(db, scenario_id)
    T, _, _ = pipeline.seed_od(nodes, links, zones, body.beta)
    m = store_od(db, scenario_id, T, step="distributed", name=f"gravity beta={body.beta}")
    db.commit()
    return {"od_matrix_id": m.id, "n_zones": len(zones), "total_trips": float(T.sum())}


@router.post("/scenarios/{scenario_id}/assignment")
def run_assignment(scenario_id: str, body: AssignmentRequest, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, links = loader.load_network(db, scenario_id)
    zones = _zones_or_400(db, scenario_id)
    connectors = [z["connector_node_id"] for z in zones]

    od = (db.query(ODMatrix).filter(ODMatrix.id == body.od_matrix_id).first()
          if body.od_matrix_id else latest_distributed_od(db, scenario_id))
    if not od:
        raise HTTPException(400, "no OD matrix — run trip-distribution first")

    T = storage.load_matrix(od.storage_ref)
    sim, _ = _assign(nodes, links, connectors, T)
    targets = loader.load_counter_targets(db, scenario_id)
    res, metrics = store_assignment(db, scenario_id, od.id, sim, links, targets)
    db.commit()
    return {"assignment_id": res.id, "total_vkt": res.total_vkt, **metrics}


@router.post("/scenarios/{scenario_id}/run-4step")
def run_4step(scenario_id: str, body: RunFourStepRequest, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, links = loader.load_network(db, scenario_id)
    zones = _zones_or_400(db, scenario_id)
    connectors = [z["connector_node_id"] for z in zones]

    T, _, _ = pipeline.seed_od(nodes, links, zones, body.beta)
    m = store_od(db, scenario_id, T, step="distributed", name=f"gravity beta={body.beta}")
    sim, _ = _assign(nodes, links, connectors, T)
    targets = loader.load_counter_targets(db, scenario_id)
    res, metrics = store_assignment(db, scenario_id, m.id, sim, links, targets)
    db.commit()
    return {"od_matrix_id": m.id, "assignment_id": res.id, "total_vkt": res.total_vkt, **metrics}
