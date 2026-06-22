from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..helpers import network_fc
from ..models import Link, Node
from ..schemas import BBoxImport, SampleNetworkRequest
from ..services import loader, osm_import
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["network"])


def _clear(db: Session, scenario_id: str) -> None:
    db.query(Link).filter(Link.scenario_id == scenario_id).delete()
    db.query(Node).filter(Node.scenario_id == scenario_id).delete()
    db.flush()


@router.post("/scenarios/{scenario_id}/network/load-sample")
def load_sample(scenario_id: str, body: SampleNetworkRequest, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    _clear(db, scenario_id)
    nodes, links, _ = osm_import.build_sample_network(
        rows=body.rows, cols=body.cols, lat0=body.lat0, lon0=body.lon0,
        spacing_m=body.spacing_m, free_flow_speed_ms=body.free_flow_speed_ms, lanes=body.lanes,
    )
    loader.persist_network(db, scenario_id, nodes, links)
    db.commit()
    return {"n_nodes": len(nodes), "n_links": len(links)}


@router.post("/scenarios/{scenario_id}/network/import-osm")
def import_osm(scenario_id: str, body: BBoxImport, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, links = osm_import.import_bbox(body.south, body.west, body.north, body.east)
    _clear(db, scenario_id)
    loader.persist_network(db, scenario_id, nodes, links)
    db.commit()
    return {"n_nodes": len(nodes), "n_links": len(links)}


@router.post("/scenarios/{scenario_id}/network/import-geojson")
def import_geojson(scenario_id: str, fc: dict = Body(...), db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, links = osm_import.parse_geojson(fc)
    _clear(db, scenario_id)
    loader.persist_network(db, scenario_id, nodes, links)
    db.commit()
    return {"n_nodes": len(nodes), "n_links": len(links)}


@router.get("/scenarios/{scenario_id}/network")
def get_network(scenario_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, links = loader.load_network(db, scenario_id)
    return network_fc(nodes, links)
