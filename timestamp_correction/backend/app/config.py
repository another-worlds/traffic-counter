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

    # Ideal-day 1m hour presence map (primary) vs legacy sync map.
    use_hour_presence_map: bool = True
    use_coherence_map: bool = True  # deprecated alias for use_hour_presence_map
    use_sync_map: bool = False
    sync_bin_duration_s: float = 60.0
    sync_tolerance_s: float = 5.0
    sync_stable_bins_required: int = 2

    # Predetermined ideal wall-clock window mapped onto each video.
    ideal_day_start: str = "00:00:00"
    ideal_day_end: str = "24:00:00"
    coherence_tolerance_s: float = 5.0  # unused by hour presence map; kept for env compat
    ideal_day_date_mode: str = "first_osd"  # first_osd | fixed
    ideal_day_date: str = ""  # YYYY-MM-DD when ideal_day_date_mode=fixed
    # realtime = 1:1 OSD vs wall clock from first reading; stretch = fit video duration to 00:00–24:00
    ideal_map_mode: str = "realtime"

    @model_validator(mode="before")
    @classmethod
    def _legacy_hour_presence_map(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "USE_HOUR_PRESENCE_MAP" in os.environ:
                data.setdefault("use_hour_presence_map", os.environ["USE_HOUR_PRESENCE_MAP"].lower() in ("1", "true", "yes"))
            if "USE_COHERENCE_MAP" in os.environ and "USE_HOUR_PRESENCE_MAP" not in os.environ:
                data.setdefault("use_hour_presence_map", os.environ["USE_COHERENCE_MAP"].lower() in ("1", "true", "yes"))
            if "use_hour_presence_map" in data:
                data.setdefault("use_coherence_map", data["use_hour_presence_map"])
            elif "use_coherence_map" in data:
                data.setdefault("use_hour_presence_map", data["use_coherence_map"])
        return data

    @model_validator(mode="before")
    @classmethod
    def _legacy_coherence_tolerance(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if os.environ.get("SYNC_TOLERANCE_S") and not os.environ.get("COHERENCE_TOLERANCE_S"):
                data.setdefault("coherence_tolerance_s", float(os.environ["SYNC_TOLERANCE_S"]))
        return data

    # seek = decode only sample frames (fast); sequential = grab every frame (slow, HEVC-safe).
    timeline_sample_mode: str = "seek"
    progress_write_interval_s: float = 3.0

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
    device: str = "cuda:0"
    cors_origins: str = "*"

    class Config:
        env_file = ".env"


settings = Settings()