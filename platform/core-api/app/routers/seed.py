"""POST /scenarios/{sid}/seed-demo — populate a scenario with a demo network.

Owns the HTTP contract: a 409 when the scenario already has content (unless
``reset=true``), a 400 for an unknown preset. The actual build lives in ``app/seed.py``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas, seed
from ..db import get_db
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["seed"])


@router.post("/scenarios/{sid}/seed-demo", response_model=schemas.SeedResult, status_code=201)
def seed_demo_scenario(
    sid: str,
    preset: str = Query("grid3x3"),
    reset: bool = Query(False),
    db: Session = Depends(get_db),
):
    scenario = get_scenario_or_404(db, sid)

    already = db.execute(
        select(models.Node.id).where(models.Node.scenario_id == sid).limit(1)
    ).first() is not None
    if already and not reset:
        raise HTTPException(
            status_code=409,
            detail="scenario already has content; pass reset=true to rebuild",
        )

    try:
        counts = seed.seed_demo(db, scenario, preset=preset, reset=reset)
    except ValueError as exc:  # unknown preset
        raise HTTPException(status_code=400, detail=str(exc))

    return schemas.SeedResult(scenario_id=sid, **counts)
