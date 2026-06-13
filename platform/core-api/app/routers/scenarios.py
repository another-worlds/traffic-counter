"""Scenario CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db

router = APIRouter(tags=["scenarios"])


def get_scenario_or_404(db: Session, scenario_id: str) -> models.Scenario:
    sc = db.get(models.Scenario, scenario_id)
    if sc is None:
        raise HTTPException(status_code=404, detail="scenario not found")
    return sc


@router.post("/scenarios", response_model=schemas.ScenarioRead, status_code=201)
def create_scenario(payload: schemas.ScenarioCreate, db: Session = Depends(get_db)):
    sc = models.Scenario(
        name=payload.name,
        description=payload.description,
        bbox=payload.bbox,
        parent_id=payload.parent_id,
    )
    db.add(sc)
    db.commit()
    db.refresh(sc)
    return sc


@router.get("/scenarios", response_model=list[schemas.ScenarioRead])
def list_scenarios(db: Session = Depends(get_db)):
    return list(db.execute(select(models.Scenario).order_by(models.Scenario.created_at)).scalars())


@router.get("/scenarios/{scenario_id}", response_model=schemas.ScenarioRead)
def get_scenario(scenario_id: str, db: Session = Depends(get_db)):
    return get_scenario_or_404(db, scenario_id)


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str, db: Session = Depends(get_db)):
    sc = get_scenario_or_404(db, scenario_id)
    db.delete(sc)
    db.commit()
