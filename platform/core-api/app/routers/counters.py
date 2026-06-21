"""Counter→link bridge: ingest a traffic-counter directional count and snap it to a
directed link, resolving AB/BA travel direction from a compass-bearing hint.

Reuses the GiST KNN from ``routers/network.py::snap_to_link`` (here selecting the Link
entity + geodesic distance so each candidate's bearing can be compared to the hint).
``seed-demo`` builds two coincident directed links per edge (A->B and B->A); the
distance-band + bearing rule deterministically picks the one whose travel sense matches.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import geo, models, schemas
from ..db import get_db
from .network import _counter_read
from .scenarios import get_scenario_or_404

router = APIRouter(tags=["counters"])

# Candidates within this distance of the nearest are treated as ties (coincident twins).
_SNAP_BAND_M = 1.0


def _angular_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two compass bearings (degrees), 0..180."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


def _resolve_snap(db: Session, sid: str, lon: float, lat: float,
                  hint: float | None, max_candidates: int, max_snap_m: float):
    """Nearest link + resolved AB/BA direction. Returns ``(Link, distance_m, direction)``
    or ``None`` when no link lies within ``max_snap_m``."""
    pt = func.ST_SetSRID(func.ST_MakePoint(lon, lat), geo.SRID)
    dist = func.ST_Distance(func.geography(models.Link.geom), func.geography(pt)).label("dist_m")
    rows = db.execute(
        select(models.Link, dist)
        .where(models.Link.scenario_id == sid)
        .order_by(models.Link.geom.op("<->")(pt))  # GiST KNN, nearest first
        .limit(max(1, max_candidates))
    ).all()
    if not rows:
        return None
    nearest = min(float(d) for _, d in rows)
    if nearest > max_snap_m:
        return None
    band = [(lk, float(d)) for lk, d in rows if float(d) <= nearest + _SNAP_BAND_M]

    if hint is None:
        link, d = min(band, key=lambda x: x[1])
        return link, d, "AB"

    best_link, best_d, best_diff = None, None, None
    for lk, d in band:
        diff = _angular_diff(hint, geo.linestring_bearing(lk.geom))
        if best_diff is None or (diff, d) < (best_diff, best_d):
            best_link, best_d, best_diff = lk, d, diff
    direction = "AB" if best_diff <= 90.0 else "BA"
    return best_link, best_d, direction


@router.post("/scenarios/{sid}/counters/ingest",
             response_model=schemas.CounterSnapResult, status_code=201)
def ingest_counter(sid: str, payload: schemas.CounterIngest, db: Session = Depends(get_db)):
    """Snap a counting line to a directed link and store/update the Counter."""
    get_scenario_or_404(db, sid)
    snap = _resolve_snap(db, sid, payload.lon, payload.lat, payload.direction_hint_deg,
                         payload.max_candidates, payload.max_snap_m)
    if snap is None:
        raise HTTPException(status_code=422,
                            detail=f"no link within {payload.max_snap_m} m of the point")
    link, dist_m, direction = snap

    # Idempotent on provenance: re-ingesting the same counting line updates its row.
    existing = None
    if payload.source_video_id is not None and payload.source_line_id is not None:
        existing = db.execute(
            select(models.Counter).where(
                models.Counter.scenario_id == sid,
                models.Counter.source_video_id == payload.source_video_id,
                models.Counter.source_line_id == payload.source_line_id,
            )
        ).scalars().first()

    created = existing is None
    c = existing or models.Counter(scenario_id=sid)
    c.name = payload.name
    c.geom = geo.geojson_to_geom({"type": "Point", "coordinates": [payload.lon, payload.lat]})
    c.snapped_link_id = link.id
    c.link_direction = direction
    c.source_video_id = payload.source_video_id
    c.source_line_id = payload.source_line_id
    c.observed_vph = payload.observed_vph
    c.pcu_vph = payload.pcu_vph
    c.hours = payload.hours
    if created:
        db.add(c)
    db.commit()
    db.refresh(c)
    return schemas.CounterSnapResult(
        counter_id=c.id, snapped_link_id=c.snapped_link_id,
        link_direction=c.link_direction, distance_m=dist_m, created=created,
    )


@router.post("/scenarios/{sid}/counters/{counter_id}/resnap",
             response_model=schemas.CounterSnapResult)
def resnap_counter(sid: str, counter_id: str, payload: schemas.CounterResnap,
                   db: Session = Depends(get_db)):
    """Re-bind an existing counter to the nearest link (e.g. after topology edits).
    ``link_direction`` is preserved unless a fresh ``direction_hint_deg`` is supplied."""
    get_scenario_or_404(db, sid)
    c = db.get(models.Counter, counter_id)
    if c is None or c.scenario_id != sid:
        raise HTTPException(status_code=404, detail="counter not found")
    lon, lat = geo.point_xy(c.geom)
    snap = _resolve_snap(db, sid, lon, lat, payload.direction_hint_deg,
                         payload.max_candidates, payload.max_snap_m)
    if snap is None:
        raise HTTPException(status_code=422,
                            detail=f"no link within {payload.max_snap_m} m of the counter")
    link, dist_m, direction = snap
    c.snapped_link_id = link.id
    if payload.direction_hint_deg is not None:  # only override direction with a fresh hint
        c.link_direction = direction
    db.commit()
    db.refresh(c)
    return schemas.CounterSnapResult(
        counter_id=c.id, snapped_link_id=c.snapped_link_id,
        link_direction=c.link_direction, distance_m=dist_m, created=False,
    )


@router.get("/scenarios/{sid}/counters", response_model=list[schemas.CounterRead])
def list_counters(sid: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    rows = db.execute(select(models.Counter).where(models.Counter.scenario_id == sid)).scalars()
    return [_counter_read(c) for c in rows]


@router.get("/scenarios/{sid}/counters/{counter_id}", response_model=schemas.CounterRead)
def get_counter(sid: str, counter_id: str, db: Session = Depends(get_db)):
    get_scenario_or_404(db, sid)
    c = db.get(models.Counter, counter_id)
    if c is None or c.scenario_id != sid:
        raise HTTPException(status_code=404, detail="counter not found")
    return _counter_read(c)
