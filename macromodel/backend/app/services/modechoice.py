"""Step 3 — Mode choice.

MVP stub: the model is single-class (car / PCU equivalent), so mode choice is an
identity scaled by a car mode share. A multinomial-logit split (with transit skims)
is the documented upgrade path; the observed counts are already converted to PCU on
the georeferencing side so the units line up.
"""
from __future__ import annotations

import numpy as np


def car_share(T, share: float = 1.0) -> np.ndarray:
    return np.asarray(T, float) * float(share)
