"""Network CRUD over **native PostGIS geometry**, a GeoJSON ``/network`` export, and the
KNN snap endpoint that georeferences a dropped point to the nearest link.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import geo, models, schemas
from ..db import get_db
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["network"])


# --------------------------- serialisers (geom -> GeoJSON) ------------------ #
def _node_read(n: models.Node) -> schemas.NodeRead:
    return schemas.NodeRead(
        id=n.id, scenario_id=n.scenario_id, name=n.name,
        geometry=geo.geom_to_geojson(n.geom), osm_id=n.osm_id, node_type_id=n.node_type_id,
    )


def _link_read(l: models.Link) -> schemas.LinkRead:
    return schemas.LinkRead(
        id=l.id, scenario_id=l.scenario_id, name=l.name,
        from_node_id=l.from_node_id, to_node_id=l.to_node_id,
        geometry=geo.geom_to_geojson(l.geom), length_m=l.length_m, lanes=l.lanes,
        free_speed_kmh=l.free_speed_kmh, capacity_vph=l.capacity_vph, oneway=l.oneway,
        allowed_modes=l.allowed_modes, link_type_id=l.link_type_id,
    )


def _zone_read(z: models.Zone) -> schemas.ZoneRead:
    return schemas.ZoneRead(
        id=z.id, scenario_id=z.scenario_id, name=z.name,
        geometry=geo.geom_to_geojson(z.geom), centroid=geo.geom_to_geojson(z.centroid),
        population=z.population, workplaces=z.workplaces, zone_type_id=z.zone_type_id,
    )


def _counter_read(c: models.Counter) -> schemas.CounterRead:
    return schemas.CounterRead(
        id=c.id, scenario_id=c.scenario_id, name=c.name,
        geometry=geo.geom_to_geojson(c.geom), snapped_link_id=c.snapped_link_id,
        link_direction=c.link_direction, source_video_id=c.source_video_id,
        source_line_id=c.source_line_id, observed_vph=c.observed_vph,
    )


# --------------------------------- nodes ----------------------------------- #
@router.post("/scenarios/{sid}/nodes", response_model=schemas.NodeRead, status_code=201)
def create_node(sid: str, payload: schemas.NodeCreate, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    node = models.Node(
        scenario_id=sid, name=payload.name,
        geom=geo.geojson_to_geom(payload.geometry),
        osm_id=payload.osm_id, node_type_id=payload.node_type_id,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    return _node_read(node)


@router.get("/scenarios/{sid}/nodes", response_model=list[schemas.NodeRead])
def list_nodes(sid: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    rows = db.execute(select(models.Node).where(models.Node.scenario_id == sid)).scalars()
    return [_node_read(n) for n in rows]


# --------------------------------- links ----------------------------------- #
@router.post("/scenarios/{sid}/links", response_model=schemas.LinkRead, status_code=201)
def create_link(sid: str, payload: schemas.LinkCreate, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    fn = db.get(models.Node, payload.from_node_id)
    tn = db.get(models.Node, payload.to_node_id)
    if fn is None or tn is None or fn.scenario_id != sid or tn.scenario_id != sid:
        raise HTTPException(status_code=400, detail="from/to node not found in scenario")

    # Geometry: use the supplied line, else a straight line between the two nodes.
    if payload.geometry is not None:
        geojson = payload.geometry
    else:
        geojson = geo.straight_line(geo.point_xy(fn.geom), geo.point_xy(tn.geom))
    length_m = geo.line_length_m(geojson["coordinates"])

    link = models.Link(
        scenario_id=sid, name=payload.name,
        from_node_id=payload.from_node_id, to_node_id=payload.to_node_id,
        geom=geo.geojson_to_geom(geojson), length_m=length_m,
        lanes=payload.lanes, free_speed_kmh=payload.free_speed_kmh,
        capacity_vph=payload.capacity_vph, oneway=payload.oneway,
        allowed_modes=payload.allowed_modes, osm_id=payload.osm_id,
        link_type_id=payload.link_type_id,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return _link_read(link)


@router.get("/scenarios/{sid}/links", response_model=list[schemas.LinkRead])
def list_links(sid: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    rows = db.execute(select(models.Link).where(models.Link.scenario_id == sid)).scalars()
    return [_link_read(l) for l in rows]


# --------------------------------- zones ----------------------------------- #
@router.post("/scenarios/{sid}/zones", response_model=schemas.ZoneRead, status_code=201)
def create_zone(sid: str, payload: schemas.ZoneCreate, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    geom = geo.geojson_to_geom(payload.geometry) if payload.geometry else None
    centroid = payload.centroid
    if centroid is None and payload.geometry is not None:
        from shapely.geometry import mapping, shape  # local import: only when needed
        centroid = mapping(shape(payload.geometry).centroid)
    zone = models.Zone(
        scenario_id=sid, name=payload.name, geom=geom,
        centroid=geo.geojson_to_geom(centroid) if centroid else None,
        population=payload.population, workplaces=payload.workplaces,
        zone_type_id=payload.zone_type_id,
    )
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return _zone_read(zone)


# -------------------------------- counters --------------------------------- #
@router.post("/scenarios/{sid}/counters", response_model=schemas.CounterRead, status_code=201)
def create_counter(sid: str, payload: schemas.CounterCreate, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    counter = models.Counter(
        scenario_id=sid, name=payload.name, geom=geo.geojson_to_geom(payload.geometry),
        snapped_link_id=payload.snapped_link_id, link_direction=payload.link_direction,
        source_video_id=payload.source_video_id, source_line_id=payload.source_line_id,
        observed_vph=payload.observed_vph,
    )
    db.add(counter)
    db.commit()
    db.refresh(counter)
    return _counter_read(counter)


# --------------------------- GeoJSON /network ------------------------------ #
@router.get("/scenarios/{sid}/network", response_model=schemas.FeatureCollection)
def network_geojson(sid: str, db: Session = Depends(get_db)):
    """The whole scenario network as one GeoJSON FeatureCollection (each feature tagged
    with a ``kind`` property)."""
    get_scenario_or_404(db, sid)
    feats: list[schemas.Feature] = []

    for n in db.execute(select(models.Node).where(models.Node.scenario_id == sid)).scalars():
        feats.append(schemas.Feature(id=n.id, geometry=geo.geom_to_geojson(n.geom),
                                     properties={"kind": "node", "name": n.name}))
    for l in db.execute(select(models.Link).where(models.Link.scenario_id == sid)).scalars():
        feats.append(schemas.Feature(id=l.id, geometry=geo.geom_to_geojson(l.geom),
                                     properties={"kind": "link", "name": l.name,
                                                 "length_m": l.length_m, "lanes": l.lanes,
                                                 "from_node_id": l.from_node_id,
                                                 "to_node_id": l.to_node_id}))
    for z in db.execute(select(models.Zone).where(models.Zone.scenario_id == sid)).scalars():
        g = geo.geom_to_geojson(z.geom) or geo.geom_to_geojson(z.centroid)
        feats.append(schemas.Feature(id=z.id, geometry=g,
                                     properties={"kind": "zone", "name": z.name,
                                                 "population": z.population,
                                                 "workplaces": z.workplaces}))
    for c in db.execute(select(models.Counter).where(models.Counter.scenario_id == sid)).scalars():
        feats.append(schemas.Feature(id=c.id, geometry=geo.geom_to_geojson(c.geom),
                                     properties={"kind": "counter", "name": c.name,
                                                 "snapped_link_id": c.snapped_link_id,
                                                 "observed_vph": c.observed_vph}))
    return schemas.FeatureCollection(features=feats)


# ------------------------------ KNN snap ----------------------------------- #
@router.post("/scenarios/{sid}/snap", response_model=schemas.SnapResult)
def snap_to_link(sid: str, payload: schemas.SnapRequest, db: Session = Depends(get_db)):
    """Nearest link to a dropped point. GiST KNN (``geom <-> point``) fetches candidates,
    re-ranked by exact geodesic distance — the foundation of the counts->link bridge."""
    get_scenario_or_404(db, sid)
    # Build the probe point entirely in SQL from float params (psycopg-adaptable),
    # rather than binding a WKTElement object.
    pt = func.ST_SetSRID(func.ST_MakePoint(payload.lon, payload.lat), geo.SRID)
    stmt = (
        select(
            models.Link.id,
            models.Link.name,
            func.ST_Distance(func.geography(models.Link.geom), func.geography(pt)).label("dist_m"),
        )
        .where(models.Link.scenario_id == sid)
        .order_by(models.Link.geom.op("<->")(pt))  # index-assisted nearest-first
        .limit(max(1, payload.max_candidates))
    )
    rows = db.execute(stmt).all()
    if not rows:
        raise HTTPException(status_code=404, detail="no links in scenario")
    best = min(rows, key=lambda r: r.dist_m)
    return schemas.SnapResult(link_id=best.id, name=best.name, distance_m=float(best.dist_m))
