from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Counter, Link, Node, Scenario, Zone
from ..schemas import ScenarioCreate, ScenarioOut
from ..services import demo as demo_service
from ..services import seeds

router = APIRouter(tags=["scenarios"])


def _out(db: Session, sc: Scenario) -> ScenarioOut:
    return ScenarioOut(
        id=sc.id, name=sc.name, description=sc.description, bbox=sc.bbox, created_at=sc.created_at,
        n_nodes=db.query(Node).filter(Node.scenario_id == sc.id).count(),
        n_links=db.query(Link).filter(Link.scenario_id == sc.id).count(),
        n_zones=db.query(Zone).filter(Zone.scenario_id == sc.id).count(),
        n_counters=db.query(Counter).filter(Counter.scenario_id == sc.id).count(),
    )


def get_scenario_or_404(db: Session, scenario_id: str) -> Scenario:
    sc = db.query(Scenario).filter(Scenario.id == scenario_id).first()
    if not sc:
        raise HTTPException(404, "scenario not found")
    return sc


@router.post("/scenarios", response_model=ScenarioOut)
def create_scenario(body: ScenarioCreate, db: Session = Depends(get_db)):
    sc = Scenario(name=body.name, description=body.description, bbox=body.bbox)
    db.add(sc)
    db.flush()
    seeds.seed_defaults(db, sc.id)  # link/node/zone types, modes, activities, layers, procedures
    db.commit()
    return _out(db, sc)


@router.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios(db: Session = Depends(get_db)):
    return [_out(db, sc) for sc in db.query(Scenario).order_by(Scenario.created_at.desc()).all()]


@router.get("/scenarios/{scenario_id}", response_model=ScenarioOut)
def get_scenario(scenario_id: str, db: Session = Depends(get_db)):
    return _out(db, get_scenario_or_404(db, scenario_id))


@router.patch("/scenarios/{scenario_id}", response_model=ScenarioOut)
def update_scenario(scenario_id: str, body: dict = Body(default={}), db: Session = Depends(get_db)):
    sc = get_scenario_or_404(db, scenario_id)
    if body.get("name"):
        sc.name = body["name"]
    if "description" in body:
        sc.description = body["description"]
    db.commit()
    return _out(db, sc)


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str, db: Session = Depends(get_db)):
    sc = get_scenario_or_404(db, scenario_id)
    db.delete(sc)
    db.commit()


@router.post("/scenarios/demo", response_model=ScenarioOut)
def create_demo(db: Session = Depends(get_db)):
    """Build the real Sioux Falls benchmark scenario (network + OD + detectors)."""
    from ..services import siouxfalls
    scenario_id = siouxfalls.build_sioux_falls(db)
    return _out(db, get_scenario_or_404(db, scenario_id))
