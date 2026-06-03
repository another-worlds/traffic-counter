"""Step 1 — Trip generation.

MVP: productions/attractions come from zone attributes (set by the user or by the
demo seeder). We only balance the totals so the doubly-constrained gravity model is
well posed (ΣP == ΣA). A land-use/regression generator is the upgrade path.
"""
from __future__ import annotations

from typing import List, Tuple

import numpy as np


def balance(P, A) -> Tuple[np.ndarray, np.ndarray]:
    P = np.asarray(P, float).copy()
    A = np.asarray(A, float).copy()
    if A.sum() > 0:
        A *= P.sum() / A.sum()
    return P, A


def from_zones(zones: List[dict]) -> Tuple[np.ndarray, np.ndarray]:
    P = np.array([float(z.get("production") or 0.0) for z in zones], float)
    A = np.array([float(z.get("attraction") or 0.0) for z in zones], float)
    return balance(P, A)
