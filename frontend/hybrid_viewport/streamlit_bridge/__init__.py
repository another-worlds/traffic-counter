from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).resolve().parent
_FRONTEND_DIST_DIR = _COMPONENT_DIR.parent / "dist"
_COMPONENT_NAME = "traffic_counter_hybrid_viewport"


@lru_cache(maxsize=1)
def _declare_component() -> Any:
    dev_url = os.environ.get("HYBRID_VIEWPORT_DEV_URL")
    if dev_url:
        return components.declare_component(_COMPONENT_NAME, url=dev_url)
    if _FRONTEND_DIST_DIR.exists():
        return components.declare_component(_COMPONENT_NAME, path=str(_FRONTEND_DIST_DIR))
    return None


def render_hybrid_viewport(*, bootstrap: Dict[str, Any], key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Render the React viewport through a Streamlit custom component.

    In development, set HYBRID_VIEWPORT_DEV_URL to the Vite dev server URL.
    In production, build frontend/hybrid_viewport so dist assets are available.
    """
    component = _declare_component()
    if component is None:
        components.html(
            """
            <div style='padding:12px;border:1px solid rgba(255,255,255,0.18);border-radius:12px;background:#121a28;color:#f4f7fb;font-family:Segoe UI,Arial,sans-serif;'>
              Hybrid overlay component is not configured.<br/>
              Set HYBRID_VIEWPORT_DEV_URL for development or build frontend/hybrid_viewport so dist assets exist.
            </div>
            """,
            height=120,
            scrolling=False,
        )
        return None

    value = component(bootstrap=bootstrap, key=key, default=None)
    if isinstance(value, dict):
        return value
    return None
