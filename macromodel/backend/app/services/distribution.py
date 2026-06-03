"""Step 2 — Trip distribution (doubly-constrained gravity model).

Skims are network shortest-path travel times (minutes) between zone connector nodes;
the deterrence function is exponential f(c)=exp(-beta*c). Furness/IPF balancing makes
row sums match productions and column sums match attractions.
"""
from __future__ import annotations

from typing import List

import networkx as nx
import numpy as np


def skim_minutes(G: nx.DiGraph, connector_node_ids: List[str]) -> np.ndarray:
    """Zone-to-zone shortest-path travel time in minutes."""
    n = len(connector_node_ids)
    C = np.full((n, n), np.inf)
    idx = {nid: i for i, nid in enumerate(connector_node_ids)}
    for i, o in enumerate(connector_node_ids):
        lengths = nx.single_source_dijkstra_path_length(G, o, weight="weight")
        for nid, secs in lengths.items():
            if nid in idx:
                C[i, idx[nid]] = secs / 60.0
    # Intrazonal cost: half the smallest interzonal time (a standard heuristic).
    for i in range(n):
        row = [C[i, j] for j in range(n) if j != i and np.isfinite(C[i, j])]
        C[i, i] = (min(row) / 2.0) if row else 1.0
    return C


def gravity(P, A, C_minutes, beta: float, iters: int = 50) -> np.ndarray:
    """Doubly-constrained gravity. Returns an OD matrix with zeroed diagonal."""
    P = np.asarray(P, float).copy()
    A = np.asarray(A, float).copy()
    if A.sum() > 0:
        A *= P.sum() / A.sum()  # balance totals
    F = np.exp(-beta * np.asarray(C_minutes, float))
    F[~np.isfinite(F)] = 0.0
    # Exclude intrazonal trips from distribution *before* balancing so the Furness
    # factors match the productions/attractions on the off-diagonal (zeroing the
    # diagonal afterwards would break the marginal totals).
    np.fill_diagonal(F, 0.0)
    n = len(P)
    a = np.ones(n)
    b = np.ones(n)
    for _ in range(iters):
        a = 1.0 / np.maximum((b[None, :] * A[None, :] * F).sum(axis=1), 1e-12)
        b = 1.0 / np.maximum((a[:, None] * P[:, None] * F).sum(axis=0), 1e-12)
    return (a[:, None] * P[:, None]) * (b[None, :] * A[None, :]) * F
