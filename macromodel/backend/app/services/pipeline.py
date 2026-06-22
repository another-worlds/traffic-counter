"""Pure-compute orchestration of the 4-step model (no DB), so it can be unit-tested
and reused by both the API routers and the demo seeder.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple

import numpy as np

from . import assignment, calibration, distribution, generation, modechoice, netconvert


def seed_od(nodes: List[dict], links: List[dict], zones: List[dict], beta: float):
    """Generation + distribution + (car) mode choice -> seed OD matrix.

    Returns (T, connector_node_ids, networkx_graph).
    """
    connectors = [z["connector_node_id"] for z in zones]
    P, A = generation.from_zones(zones)
    G = netconvert.build_graph(nodes, links)
    C = distribution.skim_minutes(G, connectors)
    T = modechoice.car_share(distribution.gravity(P, A, C, beta))
    return T, connectors, G


def make_assign_fn(nodes, links, connectors, deltan: int, tmax: int) -> Callable[[np.ndarray], Dict[str, float]]:
    def fn(T):
        sim, _ = assignment.assign(nodes, links, connectors, T, deltan=deltan, tmax=tmax)
        return sim
    return fn


def total_vkt(sim: Dict[str, float], links: List[dict]) -> float:
    length = {l["id"]: float(l.get("length_m") or 0.0) for l in links}
    return float(sum(v * length.get(lid, 0.0) for lid, v in sim.items()) / 1000.0)


def evaluate(sim: Dict[str, float], targets: List[dict]) -> dict:
    """GEH/RMSE of simulated vs observed at counter links. targets: [{link_id,target_vph}]."""
    if not targets:
        return {"mean_geh": None, "pct_geh_lt5": None, "rmse": None}
    m = np.array([sim.get(t["link_id"], 0.0) for t in targets], float)
    c = np.array([float(t["target_vph"]) for t in targets], float)
    g = calibration.geh(m, c)
    return {
        "mean_geh": float(np.mean(g)),
        "pct_geh_lt5": float(np.mean(g < 5) * 100.0),
        "rmse": float(np.sqrt(np.mean((m - c) ** 2))),
    }
