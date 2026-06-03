"""Local artifact storage for OD matrices and link-flow results (parquet).

Mirrors the traffic-counter's "DB holds metadata, volume holds artifacts" split
(api/app/services/storage.py). Swap for a GCS backend the same way if needed.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from .config import settings


class LocalStorage:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, key: str) -> str:
        p = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        return p

    def save_matrix(self, key: str, matrix: np.ndarray) -> str:
        pd.DataFrame(np.asarray(matrix)).to_parquet(self._path(key))
        return key

    def load_matrix(self, key: str) -> np.ndarray:
        return pd.read_parquet(self._path(key)).to_numpy()

    def save_df(self, key: str, df: pd.DataFrame) -> str:
        df.to_parquet(self._path(key))
        return key

    def load_df(self, key: str) -> pd.DataFrame:
        return pd.read_parquet(self._path(key))


storage = LocalStorage(settings.storage_root)
