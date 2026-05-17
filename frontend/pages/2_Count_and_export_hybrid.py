"""Hybrid Count & Export page skeleton.

Semantic Contract Ref: counting_line_overlay/contract v0.1

This file is the planned replacement target for the advanced counting-line UX.
The Streamlit shell hosts an embedded React/Vite viewport that owns the live
line editor, synchronized frame scrubber, and overlay controls while the API
remains the source of truth for persistence and counting.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import streamlit as st

import api_client as api
from sidebar import render_sidebar
from hybrid_viewport.streamlit_bridge import render_hybrid_viewport


@dataclass(frozen=True)
class ViewportSpec:
    """Describe the hybrid overlay viewport and its API-ready inputs."""

    project_id: str
    video_ids: List[str]
    selected_line_ids: List[str]
    frame_count: int
    active_layers: Tuple[str, ...]


def build_viewport_spec(ws: Dict[str, Any], videos: List[Dict[str, Any]], lines: List[Dict[str, Any]]) -> ViewportSpec:
    """Build the state package consumed by the embedded React/Vite overlay.

    The implementation normalizes the selected workspace, selected analyzed
    videos, and persisted lines into a stable component input structure for the
    embedded React/Vite overlay.
    """
    active_layers = ["saved-lines", "frame-scrubber"]
    if lines:
        active_layers.append("direction-overlay")
    if any(v.get("status") == "analyzed" for v in videos):
        active_layers.append("counts")

    return ViewportSpec(
        project_id=str(ws["id"]),
        video_ids=[str(v["id"]) for v in videos],
        selected_line_ids=[str(ln["id"]) for ln in lines],
        frame_count=100,
        active_layers=tuple(dict.fromkeys(active_layers)),
    )


def persist_line_edit(line_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a single line change through the API.

    TODO: Implement per semantic contract counting_line_overlay/contract v0.1.
    The function must dispatch add, move, rename, recolor, and geometry updates
    to the existing FastAPI surface and return the authoritative saved payload.
    """
    raise NotImplementedError("TODO: Implement per semantic contract counting_line_overlay/contract v0.1")


def request_live_counts(project_id: str, video_ids: List[str], line_ids: List[str]) -> Dict[str, Any]:
    """Request live counts for the current overlay state.

    TODO: Implement per semantic contract counting_line_overlay/contract v0.1.
    The overlay should call this after explicit confirmation or when the host
    requests a stable recount snapshot.
    """
    raise NotImplementedError("TODO: Implement per semantic contract counting_line_overlay/contract v0.1")


def render_page() -> None:
    """Render the planning skeleton for the hybrid count-and-export page."""
    st.set_page_config(page_title="Count & Export (Hybrid)", page_icon="📏", layout="wide")
    st.title("📏 Count & Export (Hybrid)")
    st.info("Planning skeleton only: the next implementation phase will embed a React/Vite overlay into this Streamlit page.")

    ws = render_sidebar()
    if not ws:
        st.stop()

    st.caption(f"Workspace: **{ws['name']}**")

    videos = [v for v in api.list_videos(ws["id"]) if v.get("status") == "analyzed"]
    lines = api.list_lines(ws["id"])
    spec = build_viewport_spec(ws, videos, lines)

    bootstrap = {
        "spec": {
            "projectId": spec.project_id,
            "videoIds": spec.video_ids,
            "selectedLineIds": spec.selected_line_ids,
            "frameCount": spec.frame_count,
            "activeLayers": list(spec.active_layers),
        },
        "initialLines": lines,
    }

    render_hybrid_viewport(bootstrap=bootstrap, key=f"hybrid_viewport_{ws['id']}")

    st.warning("The static editor is being replaced by a hybrid viewport. See artifacts/semantic_contracts/counting_line_overlay/contract.md.")
    st.write(
        {
            "project_id": spec.project_id,
            "video_ids": spec.video_ids,
            "selected_line_ids": spec.selected_line_ids,
            "frame_count": spec.frame_count,
            "active_layers": spec.active_layers,
        }
    )

    st.success("React overlay embedded in the Streamlit page.")


render_page()
