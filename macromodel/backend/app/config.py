from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Model database (separate from the traffic-counter DB).
    database_url: str = "postgresql+psycopg://macromodel:macromodel@localhost:5433/macromodel"

    # The traffic-counter REST API — the only coupling to the counter app.
    traffic_counter_api_url: str = "http://localhost:8000"

    # Local artifact storage (OD matrices, link-flow results as parquet).
    storage_root: str = "/data"

    env: str = "dev"
    cors_origins: str = "*"

    # UXsim simulation defaults.
    uxsim_deltan: int = 5          # platoon size (vehicles per simulated packet)
    sim_duration_s: int = 3600     # tmax — 1 h so link traffic_volume ≈ vph

    # Calibration (ODME) defaults.
    max_calibration_iters: int = 8
    odme_factor_clamp: float = 1.6  # per-iteration multiplicative correction is clamped to [1/c, c]
    odme_damping: float = 0.5       # correction exponent (<1 damps overshoot / oscillation)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
