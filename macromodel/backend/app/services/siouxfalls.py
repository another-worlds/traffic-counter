"""Build the Sioux Falls benchmark scenario — a real network with a real OD matrix.

Data: bstabler/TransportationNetworks (public benchmark), bundled as sample_data/
sioux_falls.json. Node coordinates are already real lat/lon (Sioux Falls, SD). Link
free-flow speeds are derived so travel time matches the benchmark's free-flow time over
the true geographic length. Zones carry demand-strata variables (population/workplaces)
from the OD marginals; detectors come from a UXsim run on a scaled benchmark OD.
"""
from __future__ import annotations

import json
import math
import os
import uuid

import numpy as np
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Connector, Counter, Scenario, Zone
from . import assignment, loader, seeds

# backend/app/services/siouxfalls.py → backend/sample_data/sioux_falls.json
_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                     "sample_data", "sioux_falls.json")


def _haversine_m(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


def build_sioux_falls(db: Session, name: str = "Sioux Falls",
                      f_pop: float = 0.02, f_gt: float = 0.02) -> str:
    with open(_DATA) as f:
        data = json.load(f)

    sc = Scenario(name=name, description="Sioux Falls benchmark (TransportationNetworks).")
    db.add(sc)
    db.flush()
    seeds.seed_defaults(db, sc.id)

    nid = {}
    nodes = []
    for n in data["nodes"]:
        u = str(uuid.uuid4())
        nid[n["id"]] = u
        nodes.append({"id": u, "name": f"n{n['id']}", "osm_id": None,
                      "geom": {"type": "Point", "coordinates": [n["lon"], n["lat"]]}})
    coord = {n["id"]: (n["lon"], n["lat"]) for n in data["nodes"]}

    links = []
    for l in data["links"]:
        a, b = coord[l["from"]], coord[l["to"]]
        length = max(_haversine_m(a, b), 30.0)
        speed = min(max(length / (max(l["fft"], 0.1) * 60.0), 3.0), 35.0)  # match benchmark FF time
        links.append({"id": str(uuid.uuid4()), "name": f"{l['from']}->{l['to']}",
                      "from_node_id": nid[l["from"]], "to_node_id": nid[l["to"]],
                      "geom": {"type": "LineString", "coordinates": [list(a), list(b)]},
                      "length_m": length, "lanes": 2, "free_flow_speed_ms": speed,
                      "jam_density": 0.2, "capacity_vph": 3600.0, "oneway": True})
    loader.persist_network(db, sc.id, nodes, links)
    db.flush()

    od = np.array(data["od"], float)
    row, col = od.sum(axis=1), od.sum(axis=0)
    connectors = []
    for k, n in enumerate(data["nodes"]):
        z = Zone(scenario_id=sc.id, name=f"Z{n['id']:02d}",
                 centroid={"type": "Point", "coordinates": [n["lon"], n["lat"]]},
                 connector_node_id=nid[n["id"]],
                 population=float(round(row[k] * f_pop)), workplaces=float(round(col[k] * f_pop)))
        db.add(z)
        db.flush()
        db.add(Connector(scenario_id=sc.id, zone_id=z.id, node_id=nid[n["id"]], direction="both"))
        connectors.append(nid[n["id"]])

    # Ground-truth detectors: assign a scaled benchmark OD with UXsim, sample busy links.
    sim, _ = assignment.assign(nodes, links, connectors, od * f_gt,
                               deltan=settings.uxsim_deltan, tmax=settings.sim_duration_s)
    rng = np.random.default_rng(7)
    link_by = {l["id"]: l for l in links}
    busiest = [(lid, v) for lid, v in sorted(sim.items(), key=lambda kv: kv[1], reverse=True) if v > 0][:18]
    for k, (lid, v) in enumerate(busiest):
        cs = link_by[lid]["geom"]["coordinates"]
        mid = [(cs[0][0] + cs[-1][0]) / 2, (cs[0][1] + cs[-1][1]) / 2]
        noisy = max(v * float(rng.normal(1.0, 0.05)), 1.0)
        db.add(Counter(scenario_id=sc.id, name=f"D{k:02d}", snapped_link_id=lid, link_direction="AB",
                       geom={"type": "Point", "coordinates": mid},
                       observed_vph=noisy, pcu_vph=noisy, hours=1.0))
    db.commit()
    return sc.id
