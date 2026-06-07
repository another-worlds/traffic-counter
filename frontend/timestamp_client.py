"""HTTP client for the timestamp-correction API (server-side, Docker network)."""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import httpx

TIMESTAMP_API_URL = os.environ.get(
    "TIMESTAMP_CORRECTION_API_URL", "http://timestamp-correction-api:8200"
)


class TimestampAPIError(Exception):
    pass


def _client(timeout: float = 15.0) -> httpx.Client:
    return httpx.Client(base_url=TIMESTAMP_API_URL, timeout=timeout)


def _raise(r: httpx.Response) -> None:
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.json())
        except Exception:
            detail = r.text
        raise TimestampAPIError(f"{r.status_code}: {detail}")


def get_timestamp_status(video_id: str, project_id: str) -> Dict[str, Any]:
    with _client() as c:
        r = c.get(
            f"/videos/{video_id}/timestamp-status",
            params={"project_id": project_id},
        )
        _raise(r)
        return r.json()


def get_region_preview_bytes(
    video_id: str,
    project_id: str,
    *,
    timeout: float = 10.0,
) -> Optional[bytes]:
    """Fetch OCR crop JPEG for a video's timestamp region."""
    try:
        with _client(timeout=timeout) as c:
            r = c.get(
                f"/videos/{video_id}/timestamp-region-preview",
                params={"project_id": project_id},
            )
            if r.status_code == 404:
                return None
            _raise(r)
            return r.content
    except Exception:
        return None


def delete_timestamp_scan(video_id: str, project_id: str) -> Dict[str, Any]:
    """Delete timestamp analysis artifacts and reset scan to pending."""
    with _client() as c:
        r = c.delete(
            f"/videos/{video_id}/timestamp-scan",
            params={"project_id": project_id},
        )
        _raise(r)
        return r.json()


def delete_timestamp_scans_bulk(
    items: List[Dict[str, str]],
    *,
    max_workers: int = 8,
) -> Dict[str, Any]:
    """Delete timestamp analysis for many videos. Skips are not applied — caller filters."""
    if not items:
        return {"deleted": 0, "failed": []}

    deleted: List[str] = []
    failed: List[Dict[str, str]] = []
    workers = min(max_workers, max(1, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(delete_timestamp_scan, str(item["id"]), str(item["project_id"])): item
            for item in items
        }
        for fut in as_completed(futures):
            item = futures[fut]
            vid = str(item["id"])
            try:
                fut.result()
                deleted.append(vid)
            except TimestampAPIError as exc:
                failed.append({"id": vid, "filename": str(item.get("filename", vid)), "error": str(exc)})
            except Exception as exc:
                failed.append({"id": vid, "filename": str(item.get("filename", vid)), "error": str(exc)})
    return {"deleted": len(deleted), "failed": failed}


def stop_timestamp_scan(video_id: str, project_id: str) -> Dict[str, Any]:
    """Cancel an active or queued scan and disable auto-scan for this video."""
    with _client() as c:
        r = c.post(
            f"/videos/{video_id}/timestamp-scan/stop",
            params={"project_id": project_id},
            json={},
        )
        _raise(r)
        return r.json()


def health_check() -> bool:
    try:
        with _client(timeout=3.0) as c:
            r = c.get("/healthz")
            return r.status_code == 200
    except Exception:
        return False


def fetch_statuses_for_project(
    project_id: str,
    video_ids: List[str],
) -> Dict[str, Dict[str, Any]]:
    """Bulk-fetch timestamp statuses for one project."""
    if not video_ids:
        return {}
    try:
        with _client(timeout=20.0) as c:
            r = c.get(
                f"/projects/{project_id}/timestamp-statuses",
                params={"video_ids": ",".join(video_ids)},
            )
            _raise(r)
            return r.json()
    except Exception as exc:
        return {
            vid: {
                "status": "unreachable",
                "error_message": str(exc),
                "progress": None,
                "stats": None,
            }
            for vid in video_ids
        }


def fetch_statuses_for_videos(
    videos: List[Dict[str, Any]],
    *,
    max_workers: int = 4,
) -> Dict[str, Dict[str, Any]]:
    """Fetch timestamp status for analyzed videos. Returns {video_id: status_doc}."""
    analyzed = [v for v in videos if v.get("status") == "analyzed"]
    if not analyzed:
        return {}

    by_project: Dict[str, List[str]] = {}
    for v in analyzed:
        pid = str(v["project_id"])
        by_project.setdefault(pid, []).append(str(v["id"]))

    results: Dict[str, Dict[str, Any]] = {}
    workers = min(max_workers, max(1, len(by_project)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(fetch_statuses_for_project, pid, vids): pid
            for pid, vids in by_project.items()
        }
        for fut in as_completed(futures):
            results.update(fut.result())
    return results