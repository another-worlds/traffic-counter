from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Model database (separate from the traffic-counter DB).
    database_url: str = "postgresql+psycopg://macromodel:macromodel@localhost:5433/macromodel"

    # The traffic-counter REST API — the only coupling to the counter app.
    traffic_counter_api_url: str = "http://localhost:8000"
    # Browser-reachable counter API (for <img> src in the detector popup). In Docker
    # the server-side url is an internal hostname (http://api:8000) the browser can't
    # reach, so set this to the public origin; empty falls back to traffic_counter_api_url.
    traffic_counter_public_url: str = ""
    # The counter's Streamlit UI origin, used to deep-link the "Open video" button.
    traffic_counter_ui_url: str = "http://localhost:8501"

    # Local artifact storage (OD matrices, link-flow results as parquet).
    storage_root: str = "/data"

    env: str = "dev"
    cors_origins: str = "*"

    # Optional custom Overpass API endpoint (e.g. a self-hosted instance).
    # Leave empty to use the osmnx default (https://overpass-api.de/api).
    overpass_endpoint: str = ""

    # UXsim simulation defaults.
    uxsim_deltan: int = 5          # platoon size (vehicles per simulated packet)
    sim_duration_s: int = 3600     # tmax — 1 h so link traffic_volume ≈ vph

    # Calibration (ODME) defaults.
    max_calibration_iters: int = 8
    odme_factor_clamp: float = 1.6  # per-iteration multiplicative correction is clamped to [1/c, c]
    odme_damping: float = 0.5       # correction exponent (<1 damps overshoot / oscillation)

    @property
    def counter_public_url(self) -> str:
        """Browser-reachable counter API origin (falls back to the server-side URL)."""
        return self.traffic_counter_public_url or self.traffic_counter_api_url

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
