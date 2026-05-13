"""
Job runner: kicks off the per-video analysis worker.

Local: just mark the video as 'queued'. A long-running worker container with
WORKER_MODE=poll picks it up.

Cloud Run: create an execution of the Cloud Run Job with VIDEO_ID set as env var
override. Each execution scales independently and gets its own L4.
"""
from __future__ import annotations
from typing import Protocol
from ..config import settings


class JobRunner(Protocol):
    def enqueue(self, video_id: str, project_id: str) -> str: ...


class LocalJobRunner:
    """No-op enqueue; the worker container polls for status='queued' rows."""
    def enqueue(self, video_id: str, project_id: str) -> str:
        return "queued"


class CloudRunJobRunner:
    def __init__(self, project: str, region: str, job_name: str):
        self.project = project
        self.region = region
        self.job_name = job_name
        from google.cloud import run_v2
        self.client = run_v2.JobsClient()

    def enqueue(self, video_id: str, project_id: str) -> str:
        from google.cloud import run_v2
        name = (
            f"projects/{self.project}/locations/{self.region}"
            f"/jobs/{self.job_name}"
        )
        # Override env vars for this single execution
        overrides = run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    env=[
                        run_v2.EnvVar(name="VIDEO_ID", value=video_id),
                        run_v2.EnvVar(name="PROJECT_ID", value=project_id),
                        run_v2.EnvVar(name="WORKER_MODE", value="single"),
                    ]
                )
            ]
        )
        request = run_v2.RunJobRequest(name=name, overrides=overrides)
        operation = self.client.run_job(request=request)
        # Don't block — return the operation name so the API stays snappy
        return operation.operation.name


def get_job_runner() -> JobRunner:
    if settings.job_runner == "cloudrun":
        if not (settings.gcp_project and settings.worker_job_name):
            raise RuntimeError("JOB_RUNNER=cloudrun requires GCP_PROJECT and WORKER_JOB_NAME")
        return CloudRunJobRunner(
            settings.gcp_project, settings.gcp_region, settings.worker_job_name
        )
    return LocalJobRunner()
