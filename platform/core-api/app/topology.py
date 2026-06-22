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
from sqlalchemy import or_, select
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


class MergeError(Exception):
    """The two links cannot be cleanly merged (validation failure → 400)."""


def _concat_geojson(g1, g2) -> dict:
    """Concatenate two native LINESTRINGs that meet end-to-start into one GeoJSON line."""
    c1 = [list(c) for c in to_shape(g1).coords]
    c2 = [list(c) for c in to_shape(g2).coords]
    return {"type": "LineString", "coordinates": c1 + c2[1:]}


def merge_links(db: Session, sid: str, a: models.Link, b: models.Link) -> dict:
    """Merge two links that chain through a degree-2 node into one (inverse of split):
    drop the middle node, concatenate geometry, recompute length, and merge the directed
    twin pair too. Re-snaps counters onto the link that absorbed their old one."""
    if a.id == b.id:
        raise MergeError("cannot merge a link with itself")
    # Orient so first -> mid -> second.
    if a.to_node_id == b.from_node_id:
        first, second = a, b
    elif b.to_node_id == a.from_node_id:
        first, second = b, a
    else:
        raise MergeError("links do not chain at a shared node")
    mid = first.to_node_id
    if first.from_node_id == second.to_node_id:
        raise MergeError("merge would create a self-loop")

    def _find(frm, to):
        return db.execute(select(models.Link).where(
            models.Link.scenario_id == sid,
            models.Link.from_node_id == frm,
            models.Link.to_node_id == to)).scalars().first()

    first_twin = _find(mid, first.from_node_id)    # mid -> X
    second_twin = _find(second.to_node_id, mid)    # Y -> mid
    twins = [t for t in (first_twin, second_twin) if t is not None]
    if len(twins) == 1:
        raise MergeError("asymmetric twin links at the middle node")

    # Degree-2: only the merge pair (+ its twins) may touch the middle node.
    incident = db.execute(select(models.Link).where(
        models.Link.scenario_id == sid,
        or_(models.Link.from_node_id == mid, models.Link.to_node_id == mid))).scalars().all()
    allowed = {first.id, second.id} | {t.id for t in twins}
    if any(lk.id not in allowed for lk in incident):
        raise MergeError("middle node is not degree-2 (other links attached)")

    # The middle node must be a plain pass-through (no connector/stop/zone references).
    used = (
        db.execute(select(models.Connector.id).where(
            models.Connector.scenario_id == sid, models.Connector.node_id == mid).limit(1)).first()
        or db.execute(select(models.Stop.id).where(
            models.Stop.scenario_id == sid, models.Stop.node_id == mid).limit(1)).first()
        or db.execute(select(models.Zone.id).where(
            models.Zone.scenario_id == sid, models.Zone.connector_node_id == mid).limit(1)).first()
    )
    if used:
        raise MergeError("middle node is used by a connector/stop/zone")

    merged = _child(first, sid, first.from_node_id, second.to_node_id,
                    _concat_geojson(first.geom, second.geom))
    new_links = [merged]
    removed = [first.id, second.id]
    absorbs = {first.id: merged, second.id: merged}
    if twins:  # both present (len == 2)
        merged_twin = _child(second_twin, sid, second_twin.from_node_id, first_twin.to_node_id,
                             _concat_geojson(second_twin.geom, first_twin.geom))
        new_links.append(merged_twin)
        removed += [first_twin.id, second_twin.id]
        absorbs[first_twin.id] = merged_twin
        absorbs[second_twin.id] = merged_twin

    affected = [(c, c.snapped_link_id) for c in db.execute(select(models.Counter).where(
        models.Counter.scenario_id == sid,
        models.Counter.snapped_link_id.in_(removed))).scalars()]

    db.add_all(new_links)
    db.flush()
    for lid in removed:
        db.delete(db.get(models.Link, lid))
    db.flush()
    mid_node = db.get(models.Node, mid)
    if mid_node is not None:
        db.delete(mid_node)

    for counter, old_id in affected:
        counter.snapped_link_id = absorbs[old_id].id

    db.commit()
    return {
        "new_node_id": None,
        "link_ids": [lk.id for lk in new_links],
        "removed_link_ids": removed,
        "removed_node_ids": [mid],
    }
