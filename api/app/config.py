from pydantic_settings import BaseSettings
from typing import Literal


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+psycopg://traffic:traffic@localhost:5432/traffic"

    # Storage backend
    storage_backend: Literal["local", "gcs"] = "local"
    local_storage_root: str = "/data"
    gcs_bucket: str = ""

    # Job runner backend
    job_runner: Literal["local", "cloudrun"] = "local"
    gcp_project: str = ""
    gcp_region: str = "europe-west4"
    worker_job_name: str = "traffic-counter-worker"

    # Misc
    env: Literal["dev", "prod"] = "dev"
    cors_origins: str = "*"

    # Reaper: requeue videos stuck in status='analyzing' with no heartbeat
    # for this many seconds. 30 min by default — the worker's heartbeat
    # thread pings every ~10 s, so any wait longer than this means the
    # worker process really is dead. After max_analyze_attempts requeues
    # the row finally lands in status='error' for manual intervention.
    stale_claim_threshold_seconds: int = 1800
    max_analyze_attempts: int = 3

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
