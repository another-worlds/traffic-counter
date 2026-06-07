import os
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    env: str = "dev"
    database_url: str = ""
    traffic_counter_api_url: str = "http://api:8000"
    storage_backend: str = "local"
    local_storage_root: str = "/data"
    gcs_bucket: str = ""

    # OCR one timestamp every N minutes of footage; stride in frames is fps × minutes × 60.
    sample_interval_minutes: float = 1.0

    # Ideal-vs-detected 1m sync map (stitched CCTV).
    use_sync_map: bool = True
    sync_bin_duration_s: float = 60.0
    sync_tolerance_s: float = 5.0
    sync_stable_bins_required: int = 3

    @model_validator(mode="before")
    @classmethod
    def _legacy_sample_interval_s(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if os.environ.get("SAMPLE_INTERVAL_S") and not os.environ.get("SAMPLE_INTERVAL_MINUTES"):
                data.setdefault(
                    "sample_interval_minutes",
                    float(os.environ["SAMPLE_INTERVAL_S"]) / 60.0,
                )
        return data

    @property
    def sample_interval_s(self) -> float:
        return self.sample_interval_minutes * 60.0
    min_gap_samples: int = 3
    jump_threshold_s: float = 15.0
    frozen_threshold_s: float = 30.0
    region_locator_weights: str = "/app/models/region_locator.pt"
    device: str = "cpu"
    cors_origins: str = "*"

    class Config:
        env_file = ".env"


settings = Settings()