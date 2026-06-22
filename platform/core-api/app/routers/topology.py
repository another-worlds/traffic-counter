"""Topology editing endpoints (split / merge / move). Slice 1: split link."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, topology
from ..db import get_db
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["topology"])


@router.post("/scenarios/{sid}/links/{link_id}/split",
             response_model=schemas.TopologyResult, status_code=201)
def split_link(sid: str, link_id: str, payload: schemas.SplitLinkRequest,
               db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    if payload.at is None and payload.fraction is None:
        raise HTTPException(status_code=422, detail="provide 'at' {lon,lat} or 'fraction'")
    link = db.get(models.Link, link_id)
    if link is None or link.scenario_id != sid:
        raise HTTPException(status_code=404, detail="link not found")
    try:
        return topology.split_link(db, sid, link, at=payload.at, fraction=payload.fraction)
    except topology.EndpointSplit:
        raise HTTPException(status_code=400, detail="split point is at/too near an endpoint")


@router.post("/scenarios/{sid}/links/merge",
             response_model=schemas.TopologyResult, status_code=201)
def merge_links(sid: str, payload: schemas.MergeLinksRequest, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    if len(payload.link_ids) != 2:
        raise HTTPException(status_code=422, detail="provide exactly two link_ids")
    a = db.get(models.Link, payload.link_ids[0])
    b = db.get(models.Link, payload.link_ids[1])
    if a is None or b is None or a.scenario_id != sid or b.scenario_id != sid:
        raise HTTPException(status_code=404, detail="link not found")
    try:
        return topology.merge_links(db, sid, a, b)
    except topology.MergeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
