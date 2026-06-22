"""Procedures (the function editor): CRUD + reorder + run the 4-step sequence."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Procedure
from ..services import procedures as engine
from .crud import serialize
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["procedures"])


@router.get("/scenarios/{scenario_id}/procedures")
def list_procedures(scenario_id: str, db: Session = Depends(get_db)):
    rows = (db.query(Procedure).filter(Procedure.scenario_id == scenario_id)
            .order_by(Procedure.idx).all())
    return [serialize(r) for r in rows]


@router.get("/procedures/op-types")
def op_types():
    return engine.OPS


@router.post("/scenarios/{scenario_id}/procedures")
def add_procedure(scenario_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    op_type = body.get("op_type")
    if op_type not in engine.OPS:
        raise HTTPException(400, f"unknown op_type; choose from {engine.OPS}")
    nxt = (db.query(func.max(Procedure.idx)).filter(Procedure.scenario_id == scenario_id).scalar() or -1) + 1
    p = Procedure(scenario_id=scenario_id, idx=nxt, op_type=op_type,
                  name=body.get("name", op_type), params=body.get("params", {}),
                  active=body.get("active", True))
    db.add(p)
    db.commit()
    return serialize(p)


@router.patch("/procedures/{proc_id}")
def update_procedure(proc_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    p = db.query(Procedure).filter(Procedure.id == proc_id).first()
    if not p:
        raise HTTPException(404, "procedure not found")
    for k in ("name", "params", "active", "op_type", "idx"):
        if k in body:
            setattr(p, k, body[k])
    db.commit()
    return serialize(p)


@router.delete("/procedures/{proc_id}", status_code=204)
def delete_procedure(proc_id: str, db: Session = Depends(get_db)):
    p = db.query(Procedure).filter(Procedure.id == proc_id).first()
    if p:
        db.delete(p)
        db.commit()


@router.post("/scenarios/{scenario_id}/procedures/reorder")
def reorder(scenario_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    for i, pid in enumerate(body.get("ids", [])):
        p = db.query(Procedure).filter(Procedure.id == pid, Procedure.scenario_id == scenario_id).first()
        if p:
            p.idx = i
    db.commit()
    return {"ok": True}


@router.post("/scenarios/{scenario_id}/procedures/run")
def run_all(scenario_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    return engine.run_sequence(db, scenario_id)


@router.post("/procedures/{proc_id}/run")
def run_one(proc_id: str, db: Session = Depends(get_db)):
    p = db.query(Procedure).filter(Procedure.id == proc_id).first()
    if not p:
        raise HTTPException(404, "procedure not found")
    return engine.run_sequence(db, p.scenario_id, only_id=proc_id)
