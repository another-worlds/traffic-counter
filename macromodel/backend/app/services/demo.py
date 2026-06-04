"""Build a self-contained offline demo scenario.

Creates a 4x4 grid network and 8 perimeter zones, derives a *ground-truth* OD from
true productions/attractions and a true beta, assigns it with UXsim, and turns the
busiest links into counters whose observed volumes are that ground truth (plus a
little noise). The zones' stored productions are perturbed away from the truth so the
model's seed is deliberately wrong and calibration has something to recover.
"""
from __future__ import annotations

from typing import List

import numpy as np
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Connector, Counter, Line, LineRouteStop, Scenario, Stop, Zone, ZoneDemand
from . import assignment, distribution, generation, loader, modechoice, netconvert, osm_import, seeds


def _midpoint(link: dict) -> List[float]:
    a, b = link["geom"]["coordinates"][0], link["geom"]["coordinates"][-1]
    return [(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0]


def build_demo(db: Session, name: str = "Demo: 4x4 grid", seed: int = 42) -> str:
    rng = np.random.default_rng(seed)

    sc = Scenario(name=name, description="Self-contained offline demo scenario.")
    db.add(sc)
    db.flush()
    seeds.seed_defaults(db, sc.id)  # link/node/zone types, modes, activities, layers, procedures

    nodes, links, grid = osm_import.build_sample_network(rows=4, cols=4)
    loader.persist_network(db, sc.id, nodes, links)
    db.flush()

    # 8 perimeter nodes act as zone connectors.
    perimeter = [grid[(0, 0)], grid[(0, 3)], grid[(3, 0)], grid[(3, 3)],
                 grid[(0, 1)], grid[(1, 3)], grid[(3, 2)], grid[(2, 0)]]
    base_prod = np.array([900, 800, 850, 950, 600, 650, 700, 720], float)
    base_attr = np.array([850, 900, 800, 900, 650, 600, 720, 700], float)
    # The stored (model) productions/attractions drift from the ground truth — local
    # perturbation plus a systematic 1.3x inflation — so the uncalibrated 4-step
    # over-assigns and ODME has a clear gap to close.
    perturb = rng.uniform(0.6, 1.7, size=len(perimeter))
    perturb_a = rng.uniform(0.6, 1.7, size=len(perimeter))

    by_node = {n["id"]: n for n in nodes}
    for k, nid in enumerate(perimeter):
        coords = by_node[nid]["geom"]["coordinates"]
        Ph = float(base_prod[k] * perturb[k] * 1.3)   # perturbed home productions
        Aw = float(base_attr[k] * perturb_a[k])        # perturbed work attractions
        z = Zone(
            scenario_id=sc.id, name=f"Z{k:02d}",
            centroid={"type": "Point", "coordinates": coords},
            connector_node_id=nid, production=Ph, attraction=Aw,
        )
        db.add(z)
        db.flush()
        db.add(Connector(scenario_id=sc.id, zone_id=z.id, node_id=nid, direction="both"))
        # Per-activity demand so the multi-layer 4-step (HW/WH/HO/OH) has inputs.
        db.add(ZoneDemand(scenario_id=sc.id, zone_id=z.id, activity="H", production=Ph, attraction=0.3 * Aw))
        db.add(ZoneDemand(scenario_id=sc.id, zone_id=z.id, activity="W", production=0.3 * Ph, attraction=Aw))
        db.add(ZoneDemand(scenario_id=sc.id, zone_id=z.id, activity="O", production=0.4 * Ph, attraction=0.4 * Aw))
    db.flush()

    # A little PuT data (two stops + one bus line) so the network/lists aren't empty.
    s_ids = []
    for k in (perimeter[0], perimeter[3]):
        s = Stop(scenario_id=sc.id, name=f"Stop {len(s_ids)+1}",
                 geom={"type": "Point", "coordinates": by_node[k]["geom"]["coordinates"]}, node_id=k)
        db.add(s)
        db.flush()
        s_ids.append(s.id)
    line = Line(scenario_id=sc.id, name="Bus 1", tsys="Bus", headway_min=10.0)
    db.add(line)
    db.flush()
    for i, sid in enumerate(s_ids):
        db.add(LineRouteStop(line_id=line.id, stop_id=sid, idx=i))

    # Ground truth -> observed counts.
    P_t, A_t = generation.balance(base_prod, base_attr)
    G = netconvert.build_graph(nodes, links)
    C = distribution.skim_minutes(G, perimeter)
    T_true = modechoice.car_share(distribution.gravity(P_t, A_t, C, beta=0.12))
    sim_true, _ = assignment.assign(
        nodes, links, perimeter, T_true,
        deltan=settings.uxsim_deltan, tmax=settings.sim_duration_s,
    )

    link_by_id = {l["id"]: l for l in links}
    busiest = [(lid, v) for lid, v in sorted(sim_true.items(), key=lambda kv: kv[1], reverse=True) if v > 0][:12]
    for k, (lid, v) in enumerate(busiest):
        noisy = max(v * float(rng.normal(1.0, 0.05)), 1.0)
        db.add(Counter(
            scenario_id=sc.id, name=f"C{k:02d}",
            geom={"type": "Point", "coordinates": _midpoint(link_by_id[lid])},
            snapped_link_id=lid, link_direction="AB",
            observed_vph=noisy, pcu_vph=noisy, hours=1.0,
        ))

    db.commit()
    return sc.id
