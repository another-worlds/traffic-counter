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

    # Yandex.Disk integration
    yadisk_root: str = ""  # absolute path inside the container, e.g. /yadisk

    # Misc
    env: Literal["dev", "prod"] = "dev"
    cors_origins: str = "*"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
