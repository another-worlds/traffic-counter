"""Geometric creation of network objects (the toolbar inserts) + a combined /map
endpoint returning every layer as GeoJSON for the Network editor.
"""
from __future__ import annotations

import math

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Connector, Counter, Line, LineRouteStop, LinkType, Link, Node, Stop, Zone
from ..services import georef, loader
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["network-edit"])


def _haversine_m(a, b) -> float:
    (lon1, lat1), (lon2, lat2) = a, b
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@router.post("/scenarios/{scenario_id}/network/insert-node")
def insert_node(scenario_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    n = Node(scenario_id=scenario_id, name=body.get("name", "node"),
             geom={"type": "Point", "coordinates": [body["lon"], body["lat"]]},
             node_type_id=body.get("node_type_id"))
    db.add(n)
    db.commit()
    return {"id": n.id}


@router.post("/scenarios/{scenario_id}/network/insert-link")
def insert_link(scenario_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes = {n.id: n for n in db.query(Node).filter(Node.scenario_id == scenario_id)}
    a, b = nodes.get(body["from_node_id"]), nodes.get(body["to_node_id"])
    if not a or not b:
        raise HTTPException(400, "from/to node not found")
    lt = db.query(LinkType).filter(LinkType.id == body.get("link_type_id")).first()
    v0 = (lt.v0_kmh if lt else 50.0) / 3.6
    lanes = lt.num_lanes if lt else 1
    length = _haversine_m(a.geom["coordinates"], b.geom["coordinates"])

    def mk(p, q):
        return Link(scenario_id=scenario_id, name=f"{p.name}->{q.name}",
                    from_node_id=p.id, to_node_id=q.id,
                    geom={"type": "LineString", "coordinates": [p.geom["coordinates"], q.geom["coordinates"]]},
                    length_m=length, lanes=lanes, free_flow_speed_ms=v0,
                    link_type_id=(lt.id if lt else None), oneway=bool(body.get("oneway", False)))

    created = [mk(a, b)]
    if not body.get("oneway", False):
        created.append(mk(b, a))
    for l in created:
        db.add(l)
    db.commit()
    return {"ids": [l.id for l in created]}


@router.post("/scenarios/{scenario_id}/network/insert-zone")
def insert_zone(scenario_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, _ = loader.load_network(db, scenario_id)
    connector = loader.nearest_node_id(nodes, body["lon"], body["lat"]) if nodes else None
    geom = None
    poly = body.get("polygon")
    if poly and len(poly) >= 3:  # close the ring
        ring = [list(p) for p in poly]
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        geom = {"type": "Polygon", "coordinates": [ring]}
    z = Zone(scenario_id=scenario_id, name=body.get("name", "zone"),
             centroid={"type": "Point", "coordinates": [body["lon"], body["lat"]]}, geom=geom,
             connector_node_id=connector, zone_type_id=body.get("zone_type_id"),
             production=float(body.get("production", 0.0)), attraction=float(body.get("attraction", 0.0)),
             population=float(body.get("population", 0.0)), workplaces=float(body.get("workplaces", 0.0)))
    db.add(z)
    db.flush()
    if connector:
        db.add(Connector(scenario_id=scenario_id, zone_id=z.id, node_id=connector, direction="both"))
    db.commit()
    return {"id": z.id, "connector_node_id": connector}


@router.post("/scenarios/{scenario_id}/network/select-in-bbox")
def select_in_bbox(scenario_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    """Return element ids whose geometry falls inside a lat/lon box (rubber-band select)."""
    get_scenario_or_404(db, scenario_id)
    s, w, n, e = body["south"], body["west"], body["north"], body["east"]

    def inside(lon, lat):
        return w <= lon <= e and s <= lat <= n

    nodes, links = loader.load_network(db, scenario_id)
    nin = {nd["id"] for nd in nodes if nd.get("geom") and inside(*nd["geom"]["coordinates"])}
    zones = db.query(Zone).filter(Zone.scenario_id == scenario_id).all()
    dets = db.query(Counter).filter(Counter.scenario_id == scenario_id).all()
    stops = db.query(Stop).filter(Stop.scenario_id == scenario_id).all()
    return {
        "nodes": sorted(nin),
        "links": [l["id"] for l in links if l["from_node_id"] in nin or l["to_node_id"] in nin],
        "zones": [z.id for z in zones if z.centroid and inside(*z.centroid["coordinates"])],
        "detectors": [c.id for c in dets if c.geom and inside(*c.geom["coordinates"])],
        "stops": [st.id for st in stops if st.geom and inside(*st.geom["coordinates"])],
    }


@router.post("/scenarios/{scenario_id}/network/move-node")
def move_node(scenario_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    node = db.query(Node).filter(Node.id == body["node_id"], Node.scenario_id == scenario_id).first()
    if not node:
        raise HTTPException(404, "node not found")
    lon, lat = float(body["lon"]), float(body["lat"])
    node.geom = {"type": "Point", "coordinates": [lon, lat]}
    xy = {n.id: n.geom["coordinates"] for n in db.query(Node).filter(Node.scenario_id == scenario_id) if n.geom}
    xy[node.id] = [lon, lat]
    links = db.query(Link).filter(Link.scenario_id == scenario_id,
                                  (Link.from_node_id == node.id) | (Link.to_node_id == node.id)).all()
    for l in links:
        a, b = xy.get(l.from_node_id), xy.get(l.to_node_id)
        if a and b:
            l.geom = {"type": "LineString", "coordinates": [a, b]}
            l.length_m = _haversine_m(a, b)
    db.commit()
    return {"ok": True, "links_updated": len(links)}


@router.post("/scenarios/{scenario_id}/network/insert-stop")
def insert_stop(scenario_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, _ = loader.load_network(db, scenario_id)
    node = loader.nearest_node_id(nodes, body["lon"], body["lat"]) if nodes else None
    s = Stop(scenario_id=scenario_id, name=body.get("name", "stop"),
             geom={"type": "Point", "coordinates": [body["lon"], body["lat"]]}, node_id=node)
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.post("/scenarios/{scenario_id}/network/insert-detector")
def insert_detector(scenario_id: str, body: dict = Body(...), db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes, links = loader.load_network(db, scenario_id)
    direction = (body.get("link_direction") or "AB").upper()
    lon, lat = body["lon"], body["lat"]

    # The drag-to-assign flow binds an explicit directed link the user picked on the map;
    # otherwise fall back to snapping the drop point to the nearest directed link (legacy).
    link_id = body.get("link_id")
    if link_id:
        snapped_id = link_id
    else:
        snapped = georef.snap_to_link(lon, lat, links) if links else None
        snapped_id = snapped["id"] if snapped else None
        if snapped_id and direction == "BA":
            snapped_id = loader.reverse_link_id(links, snapped_id) or snapped_id

    # Marker geometry magnetizes to the nearest node by default; "link" keeps the drop point.
    geom_lon, geom_lat = lon, lat
    if (body.get("snap") or "node").lower() == "node" and nodes:
        nid = loader.nearest_node_id(nodes, lon, lat)
        nd = next((n for n in nodes if n["id"] == nid), None)
        if nd and nd.get("geom"):
            geom_lon, geom_lat = nd["geom"]["coordinates"]

    d = Counter(scenario_id=scenario_id, name=body.get("name", "detector"),
                geom={"type": "Point", "coordinates": [geom_lon, geom_lat]},
                snapped_link_id=snapped_id, link_direction=direction,
                source_video_id=body.get("source_video_id"), source_line_id=body.get("source_line_id"),
                observed_vph=body.get("observed_vph"), pcu_vph=body.get("pcu_vph") or body.get("observed_vph"))
    db.add(d)
    db.commit()
    return {"id": d.id, "snapped_link_id": snapped_id}


def _pt(geom):
    return geom["coordinates"] if geom else None


@router.get("/scenarios/{scenario_id}/map")
def map_layers(scenario_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, scenario_id)
    nodes = db.query(Node).filter(Node.scenario_id == scenario_id).all()
    node_xy = {n.id: n.geom["coordinates"] for n in nodes if n.geom}
    links = db.query(Link).filter(Link.scenario_id == scenario_id).all()
    zones = db.query(Zone).filter(Zone.scenario_id == scenario_id).all()
    connectors = db.query(Connector).filter(Connector.scenario_id == scenario_id).all()
    stops = db.query(Stop).filter(Stop.scenario_id == scenario_id).all()
    lines = db.query(Line).filter(Line.scenario_id == scenario_id).all()
    dets = db.query(Counter).filter(Counter.scenario_id == scenario_id).all()
    zone_xy = {z.id: (z.centroid or z.geom or {}).get("coordinates") for z in zones}
    stop_xy = {s.id: s.geom["coordinates"] for s in stops if s.geom}

    def fc(feats):
        return {"type": "FeatureCollection", "features": feats}

    def feat(geom, props):
        return {"type": "Feature", "geometry": geom, "properties": props}

    connector_feats = []
    for c in connectors:
        z, n = zone_xy.get(c.zone_id), node_xy.get(c.node_id)
        if z and n:
            connector_feats.append(feat({"type": "LineString", "coordinates": [z, n]},
                                        {"id": c.id, "kind": "connector"}))
    line_feats = []
    for ln in lines:
        seq = (db.query(LineRouteStop).filter(LineRouteStop.line_id == ln.id)
               .order_by(LineRouteStop.idx).all())
        coords = [stop_xy[s.stop_id] for s in seq if s.stop_id in stop_xy]
        if len(coords) >= 2:
            line_feats.append(feat({"type": "LineString", "coordinates": coords},
                                   {"id": ln.id, "kind": "line", "name": ln.name, "color": ln.color}))

    return {
        "nodes": fc([feat(n.geom, {"id": n.id, "kind": "node", "name": n.name}) for n in nodes if n.geom]),
        "links": fc([feat(l.geom, {"id": l.id, "kind": "link", "name": l.name,
                                   "link_type_id": l.link_type_id}) for l in links if l.geom]),
        "zones": fc([feat(z.geom or z.centroid, {"id": z.id, "kind": "zone", "name": z.name,
                                                 "production": z.production, "attraction": z.attraction,
                                                 "population": z.population, "workplaces": z.workplaces,
                                                 "centroid": (z.centroid or {}).get("coordinates")})
                     for z in zones if (z.centroid or z.geom)]),
        "connectors": fc(connector_feats),
        "stops": fc([feat(s.geom, {"id": s.id, "kind": "stop", "name": s.name}) for s in stops if s.geom]),
        "lines": fc(line_feats),
        "detectors": fc([feat(d.geom, {"id": d.id, "kind": "detector", "name": d.name,
                                       "observed_vph": d.observed_vph, "pcu_vph": d.pcu_vph,
                                       "link_direction": d.link_direction,
                                       "snapped_link_id": d.snapped_link_id,
                                       "source_video_id": d.source_video_id,
                                       "source_line_id": d.source_line_id}) for d in dets if d.geom]),
    }
