"""Generic CRUD for all attribute objects (classes, demand model, and edit/delete of
network objects) driven by a registry. Geometric *creation* (node/link/zone/stop/
detector) lives in network_edit.py because it computes geometry/snapping.

Endpoints:
  GET    /scenarios/{sid}/objects/{obj}        list rows (tabular, for Lists)
  POST   /scenarios/{sid}/objects/{obj}        create (non-geometric types)
  PATCH  /objects/{obj}/{row_id}               update allowed fields
  DELETE /objects/{obj}/{row_id}               delete
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import inspect as sqla_inspect
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import (
    Activity, Connector, Counter, DemandLayer, Line, LinkType, Mode, ModeChoiceParam,
    Node, NodeType, Link, Stop, Zone, ZoneDemand, ZoneType,
)

router = APIRouter(tags=["objects"])

# obj name -> (model, settable fields, optional order column)
REGISTRY = {
    "nodes": (Node, ["name", "node_type_id"], "name"),
    "links": (Link, ["name", "link_type_id", "lanes", "free_flow_speed_ms", "v0_kmh",
                      "capacity_vph", "oneway", "allowed_modes"], "name"),
    "zones": (Zone, ["name", "zone_type_id", "production", "attraction", "population", "workplaces",
                     "connector_node_id"], "name"),
    "connectors": (Connector, ["zone_id", "node_id", "direction", "t0_min", "weight"], None),
    "stops": (Stop, ["name", "node_id"], "name"),
    "lines": (Line, ["name", "tsys", "headway_min", "color"], "name"),
    "detectors": (Counter, ["name", "snapped_link_id", "link_direction", "observed_vph",
                            "pcu_vph", "source_video_id", "source_line_id"], "name"),
    "link_types": (LinkType, ["name", "rank", "num_lanes", "capacity_vph", "v0_kmh", "allowed_modes"], "rank"),
    "node_types": (NodeType, ["name", "control"], "name"),
    "zone_types": (ZoneType, ["name", "category"], "name"),
    "modes": (Mode, ["code", "name", "is_prt", "assignment"], "code"),
    "activities": (Activity, ["code", "name", "is_home"], "code"),
    "demand_layers": (DemandLayer, ["code", "name", "from_activity", "to_activity", "beta",
                                    "prod_var", "attr_var", "trip_rate"], "code"),
    "zone_demand": (ZoneDemand, ["zone_id", "activity", "production", "attraction"], None),
    "mode_choice_params": (ModeChoiceParam, ["demand_layer", "mode_code", "asc", "beta_time"], None),
}
# These carry geometry; create them through /network/insert-*.
GEOMETRIC = {"nodes", "links", "zones", "stops", "detectors"}


def _spec(obj: str):
    if obj not in REGISTRY:
        raise HTTPException(404, f"unknown object type '{obj}'")
    return REGISTRY[obj]


def serialize(row) -> dict:
    return {c.key: getattr(row, c.key) for c in sqla_inspect(row).mapper.column_attrs}


@router.get("/scenarios/{scenario_id}/objects/{obj}")
def list_objects(scenario_id: str, obj: str, db: Session = Depends(get_db)):
    model, _, order = _spec(obj)
    q = db.query(model).filter(model.scenario_id == scenario_id)
    if order:
        q = q.order_by(getattr(model, order))
    return [serialize(r) for r in q.all()]


@router.post("/scenarios/{scenario_id}/objects/{obj}")
def create_object(scenario_id: str, obj: str, body: dict = Body(default={}), db: Session = Depends(get_db)):
    if obj in GEOMETRIC:
        raise HTTPException(400, f"create '{obj}' via /scenarios/{{id}}/network/insert-* (needs geometry)")
    model, fields, _ = _spec(obj)
    row = model(scenario_id=scenario_id, **{k: body[k] for k in fields if k in body})
    db.add(row)
    db.commit()
    return serialize(row)


@router.patch("/objects/{obj}/{row_id}")
def update_object(obj: str, row_id: str, body: dict = Body(default={}), db: Session = Depends(get_db)):
    model, fields, _ = _spec(obj)
    row = db.query(model).filter(model.id == row_id).first()
    if not row:
        raise HTTPException(404, "not found")
    for k in fields:
        if k in body:
            setattr(row, k, body[k])
    db.commit()
    return serialize(row)


@router.delete("/objects/{obj}/{row_id}", status_code=204)
def delete_object(obj: str, row_id: str, db: Session = Depends(get_db)):
    model, _, _ = _spec(obj)
    row = db.query(model).filter(model.id == row_id).first()
    if row:
        db.delete(row)
        db.commit()


@router.post("/objects/{obj}/bulk-update")
def bulk_update(obj: str, body: dict = Body(...), db: Session = Depends(get_db)):
    """Apply one attribute patch to many rows (Visum-style multi-edit)."""
    model, fields, _ = _spec(obj)
    ids = body.get("ids", [])
    patch = {k: v for k, v in (body.get("patch") or {}).items() if k in fields}
    rows = db.query(model).filter(model.id.in_(ids)).all()
    for r in rows:
        for k, v in patch.items():
            setattr(r, k, v)
    db.commit()
    return {"updated": len(rows), "fields": list(patch)}


@router.post("/objects/{obj}/bulk-delete")
def bulk_delete(obj: str, body: dict = Body(...), db: Session = Depends(get_db)):
    model, _, _ = _spec(obj)
    ids = body.get("ids", [])
    n = db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
    db.commit()
    return {"deleted": int(n)}
