"""CRUD for the Visum-style class tables (LinkType / NodeType / ZoneType).

These type rows hold the default attributes that network objects inherit (a link's
lanes/capacity/speed come from its LinkType, etc.). They are scoped per scenario.
Deletes are lenient: the ``*_type_id`` foreign keys are ``ON DELETE SET NULL``, so
removing a type simply unsets it on any object that referenced it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["classes"])


# ----------------------------- serialisers --------------------------------- #
def _link_type_read(t: models.LinkType) -> schemas.LinkTypeRead:
    return schemas.LinkTypeRead(
        id=t.id, scenario_id=t.scenario_id, name=t.name, rank=t.rank,
        num_lanes=t.num_lanes, capacity_vph=t.capacity_vph,
        free_speed_kmh=t.free_speed_kmh, allowed_modes=t.allowed_modes,
    )


def _node_type_read(t: models.NodeType) -> schemas.NodeTypeRead:
    return schemas.NodeTypeRead(id=t.id, scenario_id=t.scenario_id, name=t.name, control=t.control)


def _zone_type_read(t: models.ZoneType) -> schemas.ZoneTypeRead:
    return schemas.ZoneTypeRead(id=t.id, scenario_id=t.scenario_id, name=t.name, category=t.category)


# ------------------------------- link types -------------------------------- #
@router.post("/scenarios/{sid}/link-types", response_model=schemas.LinkTypeRead, status_code=201)
def create_link_type(sid: str, payload: schemas.LinkTypeCreate, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    kwargs = dict(
        scenario_id=sid, name=payload.name, rank=payload.rank, num_lanes=payload.num_lanes,
        capacity_vph=payload.capacity_vph, free_speed_kmh=payload.free_speed_kmh,
    )
    if payload.allowed_modes is not None:  # else let the column default (["PrT","PuT"]) fire
        kwargs["allowed_modes"] = payload.allowed_modes
    t = models.LinkType(**kwargs)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _link_type_read(t)


@router.get("/scenarios/{sid}/link-types", response_model=list[schemas.LinkTypeRead])
def list_link_types(sid: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    rows = db.execute(select(models.LinkType).where(models.LinkType.scenario_id == sid)).scalars()
    return [_link_type_read(t) for t in rows]


@router.get("/scenarios/{sid}/link-types/{type_id}", response_model=schemas.LinkTypeRead)
def get_link_type(sid: str, type_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    t = db.get(models.LinkType, type_id)
    if t is None or t.scenario_id != sid:
        raise HTTPException(status_code=404, detail="link type not found")
    return _link_type_read(t)


@router.delete("/scenarios/{sid}/link-types/{type_id}", status_code=204)
def delete_link_type(sid: str, type_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    t = db.get(models.LinkType, type_id)
    if t is None or t.scenario_id != sid:
        raise HTTPException(status_code=404, detail="link type not found")
    db.delete(t)
    db.commit()


# ------------------------------- node types -------------------------------- #
@router.post("/scenarios/{sid}/node-types", response_model=schemas.NodeTypeRead, status_code=201)
def create_node_type(sid: str, payload: schemas.NodeTypeCreate, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    t = models.NodeType(scenario_id=sid, name=payload.name, control=payload.control)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _node_type_read(t)


@router.get("/scenarios/{sid}/node-types", response_model=list[schemas.NodeTypeRead])
def list_node_types(sid: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    rows = db.execute(select(models.NodeType).where(models.NodeType.scenario_id == sid)).scalars()
    return [_node_type_read(t) for t in rows]


@router.get("/scenarios/{sid}/node-types/{type_id}", response_model=schemas.NodeTypeRead)
def get_node_type(sid: str, type_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    t = db.get(models.NodeType, type_id)
    if t is None or t.scenario_id != sid:
        raise HTTPException(status_code=404, detail="node type not found")
    return _node_type_read(t)


@router.delete("/scenarios/{sid}/node-types/{type_id}", status_code=204)
def delete_node_type(sid: str, type_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    t = db.get(models.NodeType, type_id)
    if t is None or t.scenario_id != sid:
        raise HTTPException(status_code=404, detail="node type not found")
    db.delete(t)
    db.commit()


# ------------------------------- zone types -------------------------------- #
@router.post("/scenarios/{sid}/zone-types", response_model=schemas.ZoneTypeRead, status_code=201)
def create_zone_type(sid: str, payload: schemas.ZoneTypeCreate, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    t = models.ZoneType(scenario_id=sid, name=payload.name, category=payload.category)
    db.add(t)
    db.commit()
    db.refresh(t)
    return _zone_type_read(t)


@router.get("/scenarios/{sid}/zone-types", response_model=list[schemas.ZoneTypeRead])
def list_zone_types(sid: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    rows = db.execute(select(models.ZoneType).where(models.ZoneType.scenario_id == sid)).scalars()
    return [_zone_type_read(t) for t in rows]


@router.get("/scenarios/{sid}/zone-types/{type_id}", response_model=schemas.ZoneTypeRead)
def get_zone_type(sid: str, type_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    t = db.get(models.ZoneType, type_id)
    if t is None or t.scenario_id != sid:
        raise HTTPException(status_code=404, detail="zone type not found")
    return _zone_type_read(t)


@router.delete("/scenarios/{sid}/zone-types/{type_id}", status_code=204)
def delete_zone_type(sid: str, type_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    t = db.get(models.ZoneType, type_id)
    if t is None or t.scenario_id != sid:
        raise HTTPException(status_code=404, detail="zone type not found")
    db.delete(t)
    db.commit()
