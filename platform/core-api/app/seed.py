"""Demo-network seeder.

One call populates a scenario with a small grid network, default classes, zones +
connectors, and a demand model — so the rest of the platform (``/network``, the
assignment engine, the editor) has real, FK-valid data to work on.

Pure builder: takes a ``Session`` + ``Scenario`` and returns a dict of row counts
(keys match ``schemas.SeedResult``). The HTTP contract (status codes, the ``reset``
guard) lives in ``routers/seed.py``. The whole build is one transaction (a single
trailing ``commit``), so a ``reset`` wipe + failed rebuild rolls back cleanly.
"""
from __future__ import annotations

import math

from sqlalchemy import delete
from sqlalchemy.orm import Session

from . import geo, models

DEFAULT_ORIGIN = (69.24, 41.31)   # Tashkent-ish; matches the contract-test coordinates
GRID_SPACING_M = 300.0
_M_PER_DEG_LAT = 111_320.0        # metres per degree latitude (WGS84, ~constant)

# Tables seed_demo owns, in FK-safe delete order (children before parents) for reset.
# Deliberately excludes Counter/Stop/Line/Matrix/results — possible user data that
# seed-demo never creates and must not destroy.
_SEED_TABLES = (
    models.ZoneDemand,
    models.Connector,
    models.Link,
    models.Zone,
    models.Node,
    models.DemandLayer,
    models.Activity,
    models.Mode,
    models.LinkType,
    models.NodeType,
    models.ZoneType,
)

# Four corner zones: (grid cell, zone-type key, population, workplaces).
_CORNER_ZONES = [
    ((0, 0), "residential", 1200.0, 200.0),   # SW
    ((2, 2), "employment", 300.0, 1500.0),    # NE
    ((0, 2), "mixed", 800.0, 600.0),          # NW
    ((2, 0), "mixed", 600.0, 900.0),          # SE
]

# Demand layers: (code, name, from_activity, to_activity, prod_var, attr_var, trip_rate).
_DEMAND_LAYERS = [
    ("HW", "Home->Work", "H", "W", "population", "workplaces", 0.4),
    ("WH", "Work->Home", "W", "H", "workplaces", "population", 0.4),
    ("HO", "Home->Other", "H", "O", "population", "workplaces", 0.3),
    ("OH", "Other->Home", "O", "H", "workplaces", "population", 0.3),
]


def _meters_to_degrees(d_m: float, lat0_deg: float) -> tuple[float, float]:
    """(dlon, dlat) degree deltas for a metre offset at latitude ``lat0``."""
    dlat = d_m / _M_PER_DEG_LAT
    dlon = d_m / (_M_PER_DEG_LAT * math.cos(math.radians(lat0_deg)))
    return dlon, dlat


def _point_geojson(lon: float, lat: float) -> dict:
    return {"type": "Point", "coordinates": [lon, lat]}


def _square_polygon(lon: float, lat: float, half_m: float, lat0: float) -> dict:
    """A small closed square POLYGON (CCW) of half-side ``half_m`` around (lon,lat)."""
    dlon, dlat = _meters_to_degrees(half_m, lat0)
    ring = [
        [lon - dlon, lat - dlat],
        [lon + dlon, lat - dlat],
        [lon + dlon, lat + dlat],
        [lon - dlon, lat + dlat],
        [lon - dlon, lat - dlat],
    ]
    return {"type": "Polygon", "coordinates": [ring]}


def _nearest_node_id(centroid_xy, node_items):
    """node_items: list of (node_id, (lon, lat)). Nearest by planar squared distance
    (fine over a ~600 m grid; lengths elsewhere use the true geodesic helper)."""
    clon, clat = centroid_xy
    best_id, best_d2 = None, float("inf")
    for nid, (lon, lat) in node_items:
        d2 = (lon - clon) ** 2 + (lat - clat) ** 2
        if d2 < best_d2:
            best_id, best_d2 = nid, d2
    return best_id


def _wipe_scenario(db: Session, sid: str) -> None:
    """Delete this scenario's seed-owned rows in FK-safe order (within the caller's
    transaction; committed by ``seed_demo``)."""
    for model in _SEED_TABLES:
        db.execute(delete(model).where(model.scenario_id == sid))
    db.flush()


def seed_demo(db: Session, scenario, preset: str = "grid3x3",
              origin: tuple[float, float] = DEFAULT_ORIGIN, reset: bool = False) -> dict:
    """Build the demo network into ``scenario``; return a dict of row counts."""
    if preset != "grid3x3":
        raise ValueError(f"unknown preset: {preset!r}")
    n = 3
    sid = scenario.id

    if reset:
        _wipe_scenario(db, sid)

    lon0, lat0 = origin
    dlon, dlat = _meters_to_degrees(GRID_SPACING_M, lat0)

    # 1) Classes first (they are FK targets for nodes/links/zones).
    link_types = [
        models.LinkType(scenario_id=sid, name="arterial", rank=2, num_lanes=2,
                        capacity_vph=1800.0, free_speed_kmh=60.0),
        models.LinkType(scenario_id=sid, name="collector", rank=3, num_lanes=1,
                        capacity_vph=1200.0, free_speed_kmh=50.0),
        models.LinkType(scenario_id=sid, name="local", rank=4, num_lanes=1,
                        capacity_vph=600.0, free_speed_kmh=30.0),
    ]
    node_types = [
        models.NodeType(scenario_id=sid, name="junction", control="uncontrolled"),
        models.NodeType(scenario_id=sid, name="signal", control="signalized"),
    ]
    zone_types = {
        "residential": models.ZoneType(scenario_id=sid, name="residential", category="residential"),
        "employment": models.ZoneType(scenario_id=sid, name="employment", category="employment"),
        "mixed": models.ZoneType(scenario_id=sid, name="mixed", category="mixed"),
    }
    for obj in (*link_types, *node_types, *zone_types.values()):
        db.add(obj)
    db.flush()  # assign class ids (client-side default fires on flush)

    arterial = link_types[0]
    junction = node_types[0]

    # 2) Grid nodes (in-memory coords kept for link geometry + connector snapping).
    node_by_cell: dict[tuple[int, int], tuple] = {}
    for j in range(n):
        for i in range(n):
            lon, lat = lon0 + i * dlon, lat0 + j * dlat
            node = models.Node(scenario_id=sid, name=f"N{i}{j}",
                               geom=geo.geojson_to_geom(_point_geojson(lon, lat)),
                               node_type_id=junction.id)
            db.add(node)
            node_by_cell[(i, j)] = (node, (lon, lat))
    db.flush()  # assign node ids for link FKs

    # 3) Links: two directed rows per 4-neighbour edge (east + north, each emitted once).
    n_links = 0
    for j in range(n):
        for i in range(n):
            for (ni, nj) in ((i + 1, j), (i, j + 1)):
                if ni >= n or nj >= n:
                    continue
                a_node, a_xy = node_by_cell[(i, j)]
                b_node, b_xy = node_by_cell[(ni, nj)]
                for (u, u_xy, v, v_xy) in ((a_node, a_xy, b_node, b_xy),
                                           (b_node, b_xy, a_node, a_xy)):
                    gj = geo.straight_line(u_xy, v_xy)
                    db.add(models.Link(
                        scenario_id=sid, name=f"{u.name}->{v.name}",
                        from_node_id=u.id, to_node_id=v.id,
                        geom=geo.geojson_to_geom(gj),
                        length_m=geo.line_length_m(gj["coordinates"]),
                        lanes=arterial.num_lanes, free_speed_kmh=arterial.free_speed_kmh,
                        capacity_vph=arterial.capacity_vph, oneway=True,
                        allowed_modes=arterial.allowed_modes, link_type_id=arterial.id,
                    ))
                    n_links += 1

    # 4) Zones + connector (to nearest node) + per-zone-per-activity demand.
    node_items = [(node.id, xy) for (node, xy) in node_by_cell.values()]
    activities = ("H", "W", "O")
    n_zones = n_connectors = n_zone_demand = 0
    for (cell, zt_key, pop, wrk) in _CORNER_ZONES:
        _cnode, (clon, clat) = node_by_cell[cell]
        # centroid offset ~120 m diagonally *outward* from the corner node
        odlon, odlat = _meters_to_degrees(120.0, lat0)
        zlon = clon + (-1 if cell[0] == 0 else 1) * odlon
        zlat = clat + (-1 if cell[1] == 0 else 1) * odlat
        zone = models.Zone(
            scenario_id=sid, name=f"Z_{zt_key}_{cell[0]}{cell[1]}",
            geom=geo.geojson_to_geom(_square_polygon(zlon, zlat, 120.0, lat0)),
            centroid=geo.geojson_to_geom(_point_geojson(zlon, zlat)),
            population=pop, workplaces=wrk, zone_type_id=zone_types[zt_key].id,
        )
        db.add(zone)
        db.flush()  # zone id for connector + zone_demand
        nearest = _nearest_node_id((zlon, zlat), node_items)
        zone.connector_node_id = nearest
        db.add(models.Connector(scenario_id=sid, zone_id=zone.id, node_id=nearest,
                                direction="both", t0_min=0.5, weight=1.0))
        n_connectors += 1
        for act in activities:
            if act == "H":
                prod, attr = pop * 0.4, 0.0
            elif act == "W":
                prod, attr = 0.0, wrk * 0.5
            else:  # O
                prod, attr = pop * 0.2, wrk * 0.2
            db.add(models.ZoneDemand(scenario_id=sid, zone_id=zone.id, activity=act,
                                     production=prod, attraction=attr))
            n_zone_demand += 1
        n_zones += 1

    # 5) Demand model: modes, activities, demand layers.
    modes = [
        models.Mode(scenario_id=sid, code="PrT", name="Private transport", is_prt=True),
        models.Mode(scenario_id=sid, code="PuT", name="Public transport", is_prt=False),
    ]
    activity_rows = [
        models.Activity(scenario_id=sid, code="H", name="Home", is_home=True),
        models.Activity(scenario_id=sid, code="W", name="Work", is_home=False),
        models.Activity(scenario_id=sid, code="O", name="Other", is_home=False),
    ]
    layer_rows = [
        models.DemandLayer(scenario_id=sid, code=code, name=name,
                           from_activity=fa, to_activity=ta,
                           prod_var=pv, attr_var=av, trip_rate=tr)
        for (code, name, fa, ta, pv, av, tr) in _DEMAND_LAYERS
    ]
    for obj in (*modes, *activity_rows, *layer_rows):
        db.add(obj)

    db.commit()

    return {
        "nodes": len(node_by_cell),
        "links": n_links,
        "zones": n_zones,
        "connectors": n_connectors,
        "link_types": len(link_types),
        "node_types": len(node_types),
        "zone_types": len(zone_types),
        "modes": len(modes),
        "activities": len(activity_rows),
        "demand_layers": len(layer_rows),
        "zone_demand": n_zone_demand,
    }
