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
    # for this many seconds. The worker's heartbeat thread writes
    # last_heartbeat_at every ~10 s for as long as its process is alive
    # (independent of YOLO/ffmpeg speed), so 120 s = 12 missed beats means
    # the worker is genuinely dead. A short threshold means an interrupted
    # analysis (container restart, crash) auto-recovers within ~2-3 min
    # instead of leaving the GPU idle. After max_analyze_attempts requeues
    # the row finally lands in status='error' for manual intervention.
    stale_claim_threshold_seconds: int = 120
    max_analyze_attempts: int = 3

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
