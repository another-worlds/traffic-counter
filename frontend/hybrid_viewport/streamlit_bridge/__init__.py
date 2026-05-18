from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).resolve().parent

# HYBRID_VIEWPORT_DIST_PATH is set in the Docker image to /srv/hybrid_viewport_dist,
# placing the Vite build OUTSIDE the ./frontend:/app bind-mount so Docker does not shadow it.
# Falls back to the package-relative dist/ for local (non-Docker) development.
_DIST_DIR = Path(os.environ.get(
    "HYBRID_VIEWPORT_DIST_PATH",
    str(_COMPONENT_DIR.parent / "dist"),
))

_VIEWPORT_NAME = "traffic_counter_hybrid_viewport"
_UPLOADER_NAME = "traffic_counter_uploader"


@lru_cache(maxsize=1)
def _declare_viewport() -> Any:
    dev_url = os.environ.get("HYBRID_VIEWPORT_DEV_URL")
    if dev_url:
        return components.declare_component(_VIEWPORT_NAME, url=dev_url)
    if _DIST_DIR.exists():
        return components.declare_component(_VIEWPORT_NAME, path=str(_DIST_DIR))
    return None


@lru_cache(maxsize=1)
def _declare_uploader() -> Any:
    dev_url = os.environ.get("UPLOADER_DEV_URL")
    if dev_url:
        return components.declare_component(_UPLOADER_NAME, url=dev_url)
    uploader_dir = _DIST_DIR / "uploader"
    if uploader_dir.exists():
        return components.declare_component(_UPLOADER_NAME, path=str(uploader_dir))
    return None


def render_hybrid_viewport(*, bootstrap: Dict[str, Any], key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Render the React counting-line viewport inside a Streamlit iframe.

    In development set HYBRID_VIEWPORT_DEV_URL to the Vite dev server URL.
    In production run docker compose up --build — the image bakes the dist.
    """
    component = _declare_viewport()
    if component is None:
        components.html(
            "<div style='padding:12px;border:1px solid rgba(255,255,255,0.18);"
            "border-radius:12px;background:#121a28;color:#f4f7fb;"
            "font-family:Segoe UI,Arial,sans-serif;'>"
            "Hybrid overlay component is not configured.<br/>"
            "Run <code>docker compose up --build</code> — the Dockerfile builds the "
            "Vite assets and places them at /srv/hybrid_viewport_dist."
            "</div>",
            height=80,
            scrolling=False,
        )
        return None

    value = component(bootstrap=bootstrap, key=key, default=None)
    if isinstance(value, dict):
        return value
    return None


def render_uploader(
    *,
    project_id: str,
    tus_endpoint: str,
    key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Render the tus resumable-upload component.

    Returns a dict with one of:
      { kind: 'progress', pct: float, uploaded: int, total: int }
      { kind: 'complete', videoId: str, filename: str }
      { kind: 'error', message: str }
    Returns None while idle.
    """
    component = _declare_uploader()
    if component is None:
        # Uploader not built yet — fall back to a plain message.
        components.html(
            "<div style='padding:8px;color:#f4f7fb;font-family:sans-serif;font-size:13px;'>"
            "Upload component not built. Run <code>docker compose up --build</code>."
            "</div>",
            height=50,
        )
        return None

    value = component(projectId=project_id, tusEndpoint=tus_endpoint, key=key, default=None)
    if isinstance(value, dict):
        return value
    return None
