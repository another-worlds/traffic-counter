"""Topology editing — pure graph-edit operations over native PostGIS geometry.

Slice 1: ``split_link`` — insert a node on a link at a projected point, replace the link
with two GMNS-consistent children (geometry split, lengths recomputed), split the directed
twin symmetrically, and re-snap any counters to the nearest child of their parent
(preserving travel direction). Merge/move land in later slices.

Ports the proven algorithm from the frozen legacy app
(``macromodel/backend/app/routers/network_edit.py::split_link``) onto native geometry.
"""
from __future__ import annotations

from geoalchemy2.shape import to_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import geo, models

_ENDPOINT_EPS_M = 1.0  # reject splits within this geodesic distance of either endpoint


class EndpointSplit(Exception):
    """The split point coincides with (or is within 1 m of) a link endpoint."""


def _child(parent: models.Link, sid: str, frm: str, to: str, gj: dict) -> models.Link:
    """Clone ``parent`` as a child link with new from/to nodes + split geometry."""
    return models.Link(
        scenario_id=sid, name=parent.name, from_node_id=frm, to_node_id=to,
        geom=geo.geojson_to_geom(gj), length_m=geo.line_length_m(gj["coordinates"]),
        lanes=parent.lanes, free_speed_kmh=parent.free_speed_kmh,
        capacity_vph=parent.capacity_vph, oneway=parent.oneway,
        allowed_modes=parent.allowed_modes, link_type_id=parent.link_type_id,
        osm_id=parent.osm_id,
    )


def split_link(db: Session, sid: str, link: models.Link, *,
               at=None, fraction: float | None = None) -> dict:
    """Split ``link`` at a projected point (``at`` = LonLat) or a 0..1 ``fraction``.
    Returns ``{new_node_id, link_ids, removed_link_ids}``. Atomic (single commit)."""
    lon = at.lon if at is not None else None
    lat = at.lat if at is not None else None
    (sx, sy), gj_a, gj_b = geo.split_linestring(link.geom, lon=lon, lat=lat, fraction=fraction)

    # Endpoint guard (geodesic) — before mutating anything.
    coords = list(to_shape(link.geom).coords)
    a_end, b_end = list(coords[0]), list(coords[-1])
    if (geo.line_length_m([[sx, sy], a_end]) < _ENDPOINT_EPS_M
            or geo.line_length_m([[sx, sy], b_end]) < _ENDPOINT_EPS_M):
        raise EndpointSplit

    mid = models.Node(scenario_id=sid, name="split",
                      geom=geo.geojson_to_geom({"type": "Point", "coordinates": [sx, sy]}))
    db.add(mid)
    db.flush()

    children_by_parent: dict[str, list[models.Link]] = {
        link.id: [_child(link, sid, link.from_node_id, mid.id, gj_a),
                  _child(link, sid, mid.id, link.to_node_id, gj_b)]
    }
    removed = [link.id]

    # Symmetric split of the directed twin, if present (its own projection of the point).
    twin = db.execute(
        select(models.Link).where(
            models.Link.scenario_id == sid,
            models.Link.from_node_id == link.to_node_id,
            models.Link.to_node_id == link.from_node_id,
        )
    ).scalars().first()
    if twin is not None:
        _t, tgj_a, tgj_b = geo.split_linestring(twin.geom, lon=sx, lat=sy)
        children_by_parent[twin.id] = [
            _child(twin, sid, twin.from_node_id, mid.id, tgj_a),
            _child(twin, sid, mid.id, twin.to_node_id, tgj_b),
        ]
        removed.append(twin.id)

    # Capture counters on the soon-to-be-removed links before the FK SET NULL fires.
    affected = [
        (c, c.snapped_link_id) for c in db.execute(
            select(models.Counter).where(
                models.Counter.scenario_id == sid,
                models.Counter.snapped_link_id.in_(removed),
            )
        ).scalars()
    ]

    new_links = [lk for pair in children_by_parent.values() for lk in pair]
    db.add_all(new_links)
    db.flush()  # assign child ids

    db.delete(link)
    if twin is not None:
        db.delete(twin)
    db.flush()

    # Re-snap each counter to the nearest child OF ITS PARENT (direction preserved —
    # children keep the parent's from->to sense).
    for counter, parent_id in affected:
        cands = children_by_parent[parent_id]
        cp = Point(*geo.point_xy(counter.geom))
        counter.snapped_link_id = min(cands, key=lambda ch: to_shape(ch.geom).distance(cp)).id

    db.commit()
    return {
        "new_node_id": mid.id,
        "link_ids": [lk.id for lk in new_links],
        "removed_link_ids": removed,
    }
