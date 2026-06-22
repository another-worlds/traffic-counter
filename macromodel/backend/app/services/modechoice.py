"""Step 3 — Mode choice.

A binary PrT/PuT logit split. PuT travel time is approximated from the PrT skim (no
PuT network assignment in the MVP): t_put ≈ put_factor · t_prt. Utilities are
U_m = ASC_m + β_time_m · t_m; the PrT share is the standard logit. ``car_share`` is kept
for the legacy single-class pipeline.
"""
from __future__ import annotations

from typing import Dict

import numpy as np

PUT_TIME_FACTOR = 1.6  # PuT door-to-door time relative to car, lumped (wait+walk+slower)


def car_share(T, share: float = 1.0) -> np.ndarray:
    return np.asarray(T, float) * float(share)


def logit_split(T, t_prt, t_put, params: Dict[str, Dict[str, float]]):
    """Split a person-trip matrix into (PrT, PuT) by a binary logit.

    params = {"PrT": {"asc":..., "beta_time":...}, "PuT": {...}}; t_* in minutes.
    """
    T = np.asarray(T, float)
    t_prt = np.asarray(t_prt, float)
    t_put = np.asarray(t_put, float)
    u_prt = params["PrT"]["asc"] + params["PrT"]["beta_time"] * t_prt
    u_put = params["PuT"]["asc"] + params["PuT"]["beta_time"] * t_put
    e_prt, e_put = np.exp(u_prt), np.exp(u_put)
    share_prt = np.where((e_prt + e_put) > 0, e_prt / (e_prt + e_put), 1.0)
    share_prt = np.where(np.isfinite(share_prt), share_prt, 1.0)
    return T * share_prt, T * (1.0 - share_prt)
