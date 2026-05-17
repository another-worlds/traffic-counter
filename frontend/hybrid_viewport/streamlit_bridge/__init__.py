from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import streamlit.components.v1 as components

_COMPONENT_DIR = Path(__file__).resolve().parent


def _load_bridge_html(*, bootstrap: Dict[str, Any]) -> str:
    template_path = _COMPONENT_DIR / "index.html"
    template = template_path.read_text(encoding="utf-8")
    return template.replace("__TRAFFIC_COUNTER_BOOTSTRAP__", json.dumps(bootstrap))


def render_hybrid_viewport(*, bootstrap: Dict[str, Any], key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Render the React viewport bridge inside a plain Streamlit HTML embed.

    The embed contains both the bridge shell and the React guest document, so
    the overlay does not depend on an externally served localhost origin.
    """
    html = _load_bridge_html(bootstrap=bootstrap)
    components.html(html, height=940, scrolling=True)
    return None
