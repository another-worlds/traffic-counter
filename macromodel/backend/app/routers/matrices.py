"""Matrix editor: list matrices, read/edit values, and basic operations."""
from __future__ import annotations

import numpy as np
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..helpers import store_od
from ..models import ODMatrix
from ..services import loader
from ..storage import storage
from .crud import serialize
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["matrices"])


def _get(db: Session, matrix_id: str) -> ODMatrix:
    m = db.query(ODMatrix).filter(ODMatrix.id == matrix_id).first()
    if not m:
        raise HTTPException(404, "matrix not found")
    return m


@router.get("/scenarios/{scenario_id}/matrices")
def list_matrices(scenario_id: str, db: Session = Depends(get_db)):
    rows = (db.query(ODMatrix).filter(ODMatrix.scenario_id == scenario_id)
            .order_by(ODMatrix.created_at.desc()).all())
    return [serialize(r) for r in rows]


@router.get("/matrices/{matrix_id}/values")
def matrix_values(matrix_id: str, db: Session = Depends(get_db)):
    m = _get(db, matrix_id)
    arr = storage.load_matrix(m.storage_ref)
    zones = loader.load_zones(db, m.scenario_id)
    labels = [z["name"] for z in zones][: arr.shape[0]]
    return {
        "id": m.id, "name": m.name, "kind": m.kind, "step": m.step,
        "n_zones": int(arr.shape[0]), "labels": labels,
        "values": np.round(arr, 2).tolist(),
        "row_sums": np.round(arr.sum(axis=1), 1).tolist(),
        "col_sums": np.round(arr.sum(axis=0), 1).tolist(),
        "total": float(np.round(arr.sum(), 1)),
    }


@router.patch("/matrices/{matrix_id}/cell")
def update_cell(matrix_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    m = _get(db, matrix_id)
    arr = storage.load_matrix(m.storage_ref)
    i, j, v = int(body["i"]), int(body["j"]), float(body["value"])
    arr[i, j] = v
    storage.save_matrix(m.storage_ref, arr)
    return {"ok": True, "total": float(arr.sum())}


@router.post("/matrices/{matrix_id}/scale")
def scale_matrix(matrix_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    m = _get(db, matrix_id)
    arr = storage.load_matrix(m.storage_ref) * float(body.get("factor", 1.0))
    storage.save_matrix(m.storage_ref, arr)
    return {"ok": True, "total": float(arr.sum())}


@router.post("/scenarios/{scenario_id}/matrices/blank")
def blank_matrix(scenario_id: str, body: dict = Body(default={}), db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    n = len(loader.load_zones(db, scenario_id))
    if n == 0:
        raise HTTPException(400, "no zones — define zones first")
    arr = np.full((n, n), float(body.get("fill", 0.0)))
    m = store_od(db, scenario_id, arr, step="loaded", name=body.get("name", "New matrix"),
                 kind=body.get("kind", "demand"))
    db.commit()
    return serialize(m)


@router.delete("/matrices/{matrix_id}", status_code=204)
def delete_matrix(matrix_id: str, db: Session = Depends(get_db)):
    m = db.query(ODMatrix).filter(ODMatrix.id == matrix_id).first()
    if m:
        db.delete(m)
        db.commit()
