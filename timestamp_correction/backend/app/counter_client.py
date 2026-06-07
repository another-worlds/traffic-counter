"""Read-only HTTP client for the traffic-counter API."""
from __future__ import annotations

from typing import Dict, List

import httpx

from .config import settings


class CounterAPIError(Exception):
    pass


def _client(timeout: float = 60.0) -> httpx.Client:
    return httpx.Client(base_url=settings.traffic_counter_api_url, timeout=timeout)


def _raise(r: httpx.Response) -> None:
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        raise CounterAPIError(f"{r.status_code}: {detail}")


def list_projects() -> List[Dict]:
    with _client() as c:
        r = c.get("/projects")
        _raise(r)
        return r.json()


def list_videos(project_id: str) -> List[Dict]:
    with _client() as c:
        r = c.get(f"/projects/{project_id}/videos")
        _raise(r)
        return r.json()


def get_video(video_id: str, timeout: float = 10.0) -> Dict:
    with _client(timeout=timeout) as c:
        r = c.get(f"/videos/{video_id}")
        _raise(r)
        return r.json()


def list_lines(video_id: str) -> List[Dict]:
    with _client(timeout=30.0) as c:
        r = c.get(f"/videos/{video_id}/lines")
        _raise(r)
        return r.json()


def compute_counts(video_id: str, line_ids: List[str]) -> Dict:
    with _client(timeout=120.0) as c:
        r = c.post(f"/videos/{video_id}/counts", json={"line_ids": line_ids})
        _raise(r)
        return r.json()