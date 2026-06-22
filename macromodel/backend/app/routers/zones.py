from __future__ import annotations

import math
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Zone
from ..schemas import AutoZonesRequest, ZoneCreate, ZoneUpdate
from ..services import loader
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["zones"])


def _spread_nodes(nodes: List[dict], n: int) -> List[dict]:
    """Pick ~n nodes spread by angle around the network centroid (cheap TAZ seeding)."""
    lons = [nd["geom"]["coordinates"][0] for nd in nodes]
    lats = [nd["geom"]["coordinates"][1] for nd in nodes]
    cx, cy = sum(lons) / len(lons), sum(lats) / len(lats)
    ordered = sorted(nodes, key=lambda nd: math.atan2(
        nd["geom"]["coordinates"][1] - cy, nd["geom"]["coordinates"][0] - cx))
    if n >= len(ordered):
        return ordered
    step = len(ordered) / n
    return [ordered[int(i * step)] for i in range(n)]


@router.get("/scenarios/{scenario_id}/zones")
def list_zones(scenario_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    feats = []
    for z in db.query(Zone).filter(Zone.scenario_id == scenario_id).order_by(Zone.name).all():
        feats.append({"type": "Feature", "geometry": z.centroid or z.geom, "properties": {
            "id": z.id, "name": z.name, "production": z.production,
            "attraction": z.attraction, "connector_node_id": z.connector_node_id}})
    return {"type": "FeatureCollection", "features": feats}


@router.post("/scenarios/{scenario_id}/zones")
def create_zone(scenario_id: str, body: ZoneCreate, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, _ = loader.load_network(db, scenario_id)
    connector = None
    if body.centroid and nodes:
        lon, lat = body.centroid["coordinates"]
        connector = loader.nearest_node_id(nodes, lon, lat)
    z = Zone(scenario_id=scenario_id, name=body.name, geom=body.geom, centroid=body.centroid,
             connector_node_id=connector, production=body.production, attraction=body.attraction)
    db.add(z)
    db.commit()
    return {"id": z.id, "connector_node_id": connector}


@router.post("/scenarios/{scenario_id}/zones/auto")
def auto_zones(scenario_id: str, body: AutoZonesRequest, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, _ = loader.load_network(db, scenario_id)
    if not nodes:
        raise HTTPException(400, "import a network first")
    db.query(Zone).filter(Zone.scenario_id == scenario_id).delete()
    db.flush()
    chosen = _spread_nodes(nodes, body.n)
    for k, nd in enumerate(chosen):
        db.add(Zone(scenario_id=scenario_id, name=f"Z{k:02d}", centroid=nd["geom"],
                    connector_node_id=nd["id"], production=500.0, attraction=500.0))
    db.commit()
    return {"n_zones": len(chosen)}


@router.patch("/zones/{zone_id}")
def update_zone(zone_id: str, body: ZoneUpdate, db: Session = Depends(get_db)):
    z = db.query(Zone).filter(Zone.id == zone_id).first()
    if not z:
        raise HTTPException(404, "zone not found")
    if body.name is not None:
        z.name = body.name
    if body.production is not None:
        z.production = body.production
    if body.attraction is not None:
        z.attraction = body.attraction
    db.commit()
    return {"id": z.id, "production": z.production, "attraction": z.attraction}
