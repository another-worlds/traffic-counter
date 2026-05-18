"""Tiny HTTP wrapper around the API."""
from __future__ import annotations
import os
import httpx
from typing import List, Dict, Optional

API_URL = os.environ.get("API_URL", "http://localhost:8000")
# URL reachable from the user's browser (differs from API_URL in Docker deployments).
PUBLIC_API_URL = os.environ.get("PUBLIC_API_URL", API_URL)


class APIError(Exception):
    pass


def _client() -> httpx.Client:
    # Increased timeout for large file uploads (can take 10+ minutes for 100GB over slow networks)
    # Timeout is per request, not per connection, so 3600s (1 hour) is reasonable for very large files
    return httpx.Client(base_url=API_URL, timeout=3600.0)


def _raise(r: httpx.Response):
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except Exception:
            detail = r.text
        raise APIError(f"{r.status_code}: {detail}")


# --- projects ---
def list_projects() -> List[Dict]:
    with _client() as c:
        r = c.get("/projects")
        _raise(r)
        return r.json()

def create_project(name: str, description: str = "") -> Dict:
    with _client() as c:
        r = c.post("/projects", json={"name": name, "description": description})
        _raise(r)
        return r.json()

def delete_project(project_id: str):
    with _client() as c:
        r = c.delete(f"/projects/{project_id}")
        _raise(r)


# --- videos ---
def list_videos(project_id: str) -> List[Dict]:
    with _client() as c:
        r = c.get(f"/projects/{project_id}/videos")
        _raise(r)
        return r.json()

def upload_video(project_id: str, filename: str, data) -> Dict:
    """Upload a video file. data can be bytes or a file-like object.

    httpx automatically:
    - Streams file-like objects without buffering
    - Sets Content-Length from file size (enables FastAPI multipart streaming)
    - Works for both bytes (<1GB) and file objects (>1GB)
    """
    with _client() as c:
        files = {"file": (filename, data, "video/mp4")}
        r = c.post(f"/projects/{project_id}/videos", files=files, timeout=None)
        _raise(r)
        return r.json()

def analyze_video(video_id: str) -> Dict:
    with _client() as c:
        r = c.post(f"/videos/{video_id}/analyze")
        _raise(r)
        return r.json()

def get_video(video_id: str) -> Dict:
    with _client() as c:
        r = c.get(f"/videos/{video_id}")
        _raise(r)
        return r.json()

def delete_video(video_id: str):
    with _client() as c:
        r = c.delete(f"/videos/{video_id}")
        _raise(r)

def list_video_frames(video_id: str) -> List[Dict]:
    """Return scene-based keyframes: [{index, time_s, frame_index_in_video, url}]."""
    with _client() as c:
        r = c.get(f"/videos/{video_id}/frames")
        _raise(r)
        return r.json()

def get_frame_url(video_id: str) -> Optional[str]:
    with _client() as c:
        r = c.get(f"/videos/{video_id}/frame-url")
        if r.status_code == 404:
            return None
        _raise(r)
        return r.json()["url"]

def get_trajectories_url(video_id: str) -> Optional[str]:
    with _client() as c:
        r = c.get(f"/videos/{video_id}/trajectories-url")
        if r.status_code == 404:
            return None
        _raise(r)
        return r.json()["url"]


def get_heatmap_url(video_id: str) -> Optional[str]:
    with _client() as c:
        r = c.get(f"/videos/{video_id}/heatmap-url")
        if r.status_code in (404, 409):
            return None
        _raise(r)
        return r.json()["url"]


def track_stats(video_id: str) -> Optional[Dict]:
    """Return aggregate track statistics for an analyzed video."""
    with _client() as c:
        r = c.get(f"/videos/{video_id}/track-stats")
        if r.status_code in (404, 409):
            return None
        _raise(r)
        return r.json()


# --- lines ---
def list_lines(project_id: str) -> List[Dict]:
    with _client() as c:
        r = c.get(f"/projects/{project_id}/lines")
        _raise(r)
        return r.json()

def create_line(project_id: str, name: str, ax: float, ay: float, bx: float, by: float,
                color: str = "#e24b4a") -> Dict:
    with _client() as c:
        r = c.post(
            f"/projects/{project_id}/lines",
            json={"name": name, "points": {"a": [ax, ay], "b": [bx, by]}, "color": color},
        )
        _raise(r)
        return r.json()

def delete_line(line_id: str):
    with _client() as c:
        r = c.delete(f"/lines/{line_id}")
        _raise(r)


def update_line(
    line_id: str,
    *,
    name: Optional[str] = None,
    color: Optional[str] = None,
    points: Optional[Dict] = None,
) -> Dict:
    payload: Dict = {}
    if name is not None:
        payload["name"] = name
    if color is not None:
        payload["color"] = color
    if points is not None:
        payload["points"] = points
    with _client() as c:
        r = c.patch(f"/lines/{line_id}", json=payload)
        _raise(r)
        return r.json()


def suggest_lines(project_id: str, video_ids: List[str], n: int = 3) -> List[Dict]:
    with _client() as c:
        r = c.post(
            f"/projects/{project_id}/suggest-lines",
            json={"video_ids": video_ids, "n": n},
        )
        _raise(r)
        return r.json()


# --- counts / export ---
def compute_counts(project_id: str, video_ids: List[str], line_ids: List[str]) -> Dict:
    with _client() as c:
        r = c.post(
            f"/projects/{project_id}/counts",
            json={"video_ids": video_ids, "line_ids": line_ids},
        )
        _raise(r)
        return r.json()

def export_xlsx(project_id: str, video_ids: List[str], line_ids: List[str]) -> bytes:
    with _client() as c:
        r = c.post(
            f"/projects/{project_id}/export",
            json={"video_ids": video_ids, "line_ids": line_ids},
        )
        _raise(r)
        return r.content


def file_url(relative: str) -> str:
    """Convert API-relative file paths (e.g. /files/...) into absolute URLs.

    Uses PUBLIC_API_URL so the browser inside the React iframe can reach the asset
    (API_URL may be an internal Docker hostname not reachable from the browser).
    """
    if relative.startswith("http"):
        return relative
    return f"{PUBLIC_API_URL}{relative}"


# --- worker / dashboard ---
def worker_status() -> List[Dict]:
    """Return videos currently queued or being analyzed."""
    with _client() as c:
        r = c.get("/worker/status")
        _raise(r)
        return r.json()


def workspace_summary(project_id: str) -> Dict:
    """Single-query aggregate stats for a workspace."""
    with _client() as c:
        r = c.get(f"/projects/{project_id}/summary")
        _raise(r)
        return r.json()


# --- local folder / watcher ---

def list_local_folder_videos(status: Optional[str] = None) -> List[Dict]:
    """Return all videos imported from the watched local folder."""
    with _client() as c:
        params = {"status": status} if status else {}
        r = c.get("/local-folder/videos", params=params)
        _raise(r)
        return r.json()


def analyze_pending_local_folder() -> Dict:
    """Queue all local-folder videos that are still in 'uploaded' state."""
    with _client() as c:
        r = c.post("/local-folder/analyze-pending")
        _raise(r)
        return r.json()
