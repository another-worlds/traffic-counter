"""Step 5 — Calibration: path-based multiplicative OD matrix estimation (ODME).

Each iteration: assign the current OD matrix, compare simulated vs observed volumes
at the counter links, then scale every OD pair by the (clamped) geometric mean of the
observed/simulated ratios on the counter links its shortest path traverses. Scored
with the GEH statistic.

``assign_fn`` is injected so this is testable with a deterministic linear assignment
operator and runs the real UXsim assignment in production.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Tuple

import networkx as nx
import numpy as np


def geh(modeled, counted) -> np.ndarray:
    m = np.asarray(modeled, float)
    c = np.asarray(counted, float)
    denom = m + c
    out = np.zeros_like(m, dtype=float)
    mask = denom > 0
    out[mask] = np.sqrt(2.0 * (m[mask] - c[mask]) ** 2 / denom[mask])
    return out


def _metrics(sim: Dict[str, float], counted_links: List[str], target: Dict[str, float], it: int) -> dict:
    m = np.array([sim.get(lid, 0.0) for lid in counted_links])
    c = np.array([target[lid] for lid in counted_links])
    g = geh(m, c)
    return {
        "iter": it,
        "mean_geh": float(np.mean(g)) if len(g) else 0.0,
        "pct_geh_lt5": float(np.mean(g < 5) * 100.0) if len(g) else 100.0,
        "rmse": float(np.sqrt(np.mean((m - c) ** 2))) if len(g) else 0.0,
    }


def _path_link_ids(G: nx.DiGraph, o: str, d: str) -> List[str]:
    try:
        path = nx.shortest_path(G, o, d, weight="weight")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return []
    return [G[u][v]["link_id"] for u, v in zip(path[:-1], path[1:])]


def build_incidence(G: nx.DiGraph, connector_node_ids: List[str], counted_links: List[str]) -> Dict[Tuple[int, int], List[str]]:
    counted = set(counted_links)
    inc: Dict[Tuple[int, int], List[str]] = {}
    n = len(connector_node_ids)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            hits = [lid for lid in _path_link_ids(G, connector_node_ids[i], connector_node_ids[j]) if lid in counted]
            if hits:
                inc[(i, j)] = hits
    return inc


def calibrate(
    T0,
    connector_node_ids: List[str],
    counters: List[dict],  # [{"link_id":..., "target_vph":...}]
    assign_fn: Callable[[np.ndarray], Dict[str, float]],
    G: nx.DiGraph,
    max_iters: int = 8,
    clamp: float = 1.6,
    damping: float = 0.5,
    eps: float = 1e-6,
) -> Tuple[np.ndarray, List[dict]]:
    counted_links = [c["link_id"] for c in counters]
    target = {c["link_id"]: float(c["target_vph"]) for c in counters}
    inc = build_incidence(G, connector_node_ids, counted_links)
    n = len(connector_node_ids)

    T = np.asarray(T0, float).copy()
    sim = assign_fn(T)
    history: List[dict] = [_metrics(sim, counted_links, target, 0)]
    best_T, best = T.copy(), history[0]

    for it in range(1, max_iters + 1):
        ratio = {lid: target[lid] / max(sim.get(lid, 0.0), eps) for lid in counted_links}
        factor = np.ones((n, n))
        for (i, j), hits in inc.items():
            rs = np.clip([ratio[lid] for lid in hits], 1e-6, None)
            f = float(np.exp(np.mean(np.log(rs))))          # geometric mean of count ratios
            f = min(max(f, 1.0 / clamp), clamp) ** damping  # clamp + damp to avoid overshoot
            factor[i, j] = f
        T = T * factor
        sim = assign_fn(T)
        m = _metrics(sim, counted_links, target, it)
        history.append(m)
        if m["mean_geh"] < best["mean_geh"]:
            best_T, best = T.copy(), m

    # The dynamic simulator can oscillate, so return the best iterate found and make
    # the final history entry reflect it (before=history[0], after=history[-1]).
    if history[-1] is not best:
        history.append({**best, "iter": "best"})
    return best_T, history
