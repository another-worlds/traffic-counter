"""ORM <-> dict helpers and DB loaders, so the pure-compute services stay
database-agnostic (and unit-testable with plain dicts).
"""
from __future__ import annotations

import math
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import Counter, Link, Node, Zone


def node_dict(n: Node) -> dict:
    return {"id": n.id, "name": n.name, "geom": n.geom, "osm_id": n.osm_id}


def link_dict(l: Link) -> dict:
    return {
        "id": l.id, "name": l.name, "from_node_id": l.from_node_id, "to_node_id": l.to_node_id,
        "geom": l.geom, "length_m": l.length_m, "lanes": l.lanes,
        "free_flow_speed_ms": l.free_flow_speed_ms, "jam_density": l.jam_density, "oneway": l.oneway,
    }


def zone_dict(z: Zone) -> dict:
    return {
        "id": z.id, "name": z.name, "centroid": z.centroid, "geom": z.geom,
        "connector_node_id": z.connector_node_id,
        "production": z.production, "attraction": z.attraction,
        "population": z.population, "workplaces": z.workplaces,
    }


def load_network(db: Session, scenario_id: str) -> Tuple[List[dict], List[dict]]:
    nodes = [node_dict(n) for n in db.query(Node).filter(Node.scenario_id == scenario_id).all()]
    links = [link_dict(l) for l in db.query(Link).filter(Link.scenario_id == scenario_id).all()]
    return nodes, links


def load_zones(db: Session, scenario_id: str) -> List[dict]:
    zones = db.query(Zone).filter(Zone.scenario_id == scenario_id).order_by(Zone.name).all()
    return [zone_dict(z) for z in zones]


def load_counter_targets(db: Session, scenario_id: str) -> List[dict]:
    """Counters that are usable as calibration targets (snapped link + PCU volume)."""
    out: List[dict] = []
    for c in db.query(Counter).filter(Counter.scenario_id == scenario_id).all():
        if c.snapped_link_id and c.pcu_vph is not None:
            out.append({"counter_id": c.id, "link_id": c.snapped_link_id, "target_vph": float(c.pcu_vph)})
    return out


def persist_network(db: Session, scenario_id: str, nodes: List[dict], links: List[dict]) -> None:
    for n in nodes:
        db.add(Node(id=n["id"], scenario_id=scenario_id, name=n.get("name"),
                    geom=n["geom"], osm_id=n.get("osm_id")))
    db.flush()  # nodes must hit the DB before links reference them — Postgres enforces the FK
    # (SQLAlchemy has no Node<->Link relationship to order these, and SQLite doesn't enforce it)
    for l in links:
        db.add(Link(id=l["id"], scenario_id=scenario_id, name=l.get("name"),
                    from_node_id=l["from_node_id"], to_node_id=l["to_node_id"], geom=l["geom"],
                    length_m=l.get("length_m", 0.0), lanes=l.get("lanes", 1),
                    free_flow_speed_ms=l.get("free_flow_speed_ms", 13.9),
                    jam_density=l.get("jam_density", 0.2), oneway=l.get("oneway", False)))


def nearest_node_id(nodes: List[dict], lon: float, lat: float) -> Optional[str]:
    best, best_d = None, float("inf")
    for n in nodes:
        nx_, ny = n["geom"]["coordinates"]
        d = (nx_ - lon) ** 2 + (ny - lat) ** 2
        if d < best_d:
            best_d, best = d, n["id"]
    return best


def reverse_link_id(links: List[dict], link_id: str) -> Optional[str]:
    """Find the opposite-direction link (to->from) for a directed link, if it exists."""
    by_id = {l["id"]: l for l in links}
    base = by_id.get(link_id)
    if not base:
        return None
    for l in links:
        if l["from_node_id"] == base["to_node_id"] and l["to_node_id"] == base["from_node_id"]:
            return l["id"]
    return None
