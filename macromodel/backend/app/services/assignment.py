"""Step 4 — Trip assignment via UXsim (the macroscopic simulation engine).

Loads an OD matrix as time-spread demand between zone connector nodes, runs the
dynamic simulation, and reads per-link traffic volume. With tmax = 3600 s, the
returned link volume is directly comparable to an hourly count (vph).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

from . import netconvert


def _link_volumes(W) -> Dict[str, float]:
    df = W.analyzer.link_to_pandas()
    vol_col = next((c for c in ("traffic_volume", "volume") if c in df.columns), None)
    name_col = "link" if "link" in df.columns else df.columns[0]
    out: Dict[str, float] = {}
    for _, row in df.iterrows():
        out[str(row[name_col])] = float(row[vol_col]) if vol_col else 0.0
    return out


def assign(
    nodes: List[dict],
    links: List[dict],
    connector_node_ids: List[str],
    T,
    deltan: int = 5,
    tmax: int = 3600,
) -> Tuple[Dict[str, float], object]:
    """Returns ({link_id: simulated_vph}, World)."""
    W, _ = netconvert.build_world(nodes, links, deltan=deltan, tmax=tmax)
    T = np.asarray(T, float)
    n = len(connector_node_ids)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            vol = float(T[i, j])
            if vol <= 0:
                continue
            W.adddemand(
                str(connector_node_ids[i]), str(connector_node_ids[j]),
                0, tmax, vol / tmax,  # veh/s spread across the hour
            )
    W.exec_simulation()
    return _link_volumes(W), W
