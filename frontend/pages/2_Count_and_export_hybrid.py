"""Hybrid Count & Export page.

Semantic Contract Ref: counting_line_overlay/contract v0.1

The Streamlit shell hosts an embedded React/Vite viewport that owns the live
line editor, synchronized frame scrubber, and overlay controls while the API
remains the source of truth for persistence and counting.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
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
    """Build the state package consumed by the embedded React/Vite overlay."""
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

    Supported actions:
    - create: payload must include project_id and line
    - update: payload must include patch
    - delete
    """
    action = payload.get("action")

    if action == "create":
        project_id = str(payload["project_id"])
        line = payload["line"]
        points = line.get("points") or []
        if len(points) < 2:
            raise ValueError("create payload must include at least two points")
        a = points[0]
        b = points[-1]
        return api.create_line(
            project_id,
            line.get("name") or "line",
            float(a[0]),
            float(a[1]),
            float(b[0]),
            float(b[1]),
            line.get("color") or "#e24b4a",
        )

    if action == "update":
        patch = dict(payload.get("patch") or {})
        points = patch.pop("points", None)
        api_points = None
        if points is not None:
            if len(points) < 2:
                raise ValueError("update points must include at least two points")
            api_points = {
                "a": [float(points[0][0]), float(points[0][1])],
                "b": [float(points[-1][0]), float(points[-1][1])],
            }
        return api.update_line(
            line_id,
            name=patch.get("name"),
            color=patch.get("color"),
            points=api_points,
        )

    if action == "delete":
        api.delete_line(line_id)
        return {"id": line_id, "deleted": True}

    raise ValueError(f"Unsupported line edit action: {action}")


def request_live_counts(project_id: str, video_ids: List[str], line_ids: List[str]) -> Dict[str, Any]:
    """Request live counts for the current overlay state.

    Returns an empty summary when there are no active lines.
    """
    if not line_ids:
        return {
            "total_unique_tracks": 0,
            "sum_across_lines": 0,
            "per_line": [],
        }
    return api.compute_counts(project_id, video_ids, line_ids)


def _points_match(api_line: Dict[str, Any], snap_line: Dict[str, Any]) -> bool:
    """Return True when the snapshot and persisted endpoints are within 0.5 px."""
    points = snap_line.get("points") or []
    if len(points) < 2:
        return False
    a = [float(points[0][0]), float(points[0][1])]
    b = [float(points[-1][0]), float(points[-1][1])]
    current = api_line.get("points") or {}
    ca = current.get("a") or [0, 0]
    cb = current.get("b") or [0, 0]
    current_a = [float(ca[0]), float(ca[1])]
    current_b = [float(cb[0]), float(cb[1])]
    tol = 0.5
    return (
        abs(current_a[0] - a[0]) <= tol and abs(current_a[1] - a[1]) <= tol
        and abs(current_b[0] - b[0]) <= tol and abs(current_b[1] - b[1]) <= tol
    )


def _line_patch(api_line: Dict[str, Any], snap_line: Dict[str, Any]) -> Dict[str, Any]:
    patch: Dict[str, Any] = {}
    if (snap_line.get("name") or "") != (api_line.get("name") or ""):
        patch["name"] = snap_line.get("name") or "line"
    if (snap_line.get("color") or "") != (api_line.get("color") or ""):
        patch["color"] = snap_line.get("color") or "#e24b4a"
    if not _points_match(api_line, snap_line):
        patch["points"] = snap_line.get("points") or []
    return patch


def _normalize_snapshot_lines(snapshot_lines: List[Dict[str, Any]], id_map: Dict[str, str]) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    for line in snapshot_lines:
        normalized_line = dict(line)
        line_id = str(normalized_line.get("id") or "")
        if line_id and line_id in id_map:
            normalized_line["id"] = id_map[line_id]
        normalized.append(normalized_line)
    return normalized


def render_page() -> None:
    """Render the hybrid count-and-export page."""
    st.set_page_config(page_title="Count & Export (Hybrid)", page_icon="📏", layout="wide")
    st.title("📏 Count & Export (Hybrid)")

    ws = render_sidebar()
    if not ws:
        st.stop()

    st.caption(f"Workspace: **{ws['name']}**")

    videos = [v for v in api.list_videos(ws["id"]) if v.get("status") == "analyzed"]
    lines = api.list_lines(ws["id"])
    spec = build_viewport_spec(ws, videos, lines)

    if not videos:
        st.info("No analyzed videos in this workspace. Analyze videos on the Videos page first.")
        st.stop()

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

    overlay_snapshot = render_hybrid_viewport(bootstrap=bootstrap, key=f"hybrid_viewport_{ws['id']}")

    if overlay_snapshot and str(overlay_snapshot.get("projectId") or "") != spec.project_id:
        overlay_snapshot = None

    if overlay_snapshot:
        snapshot_hash = json.dumps(overlay_snapshot, sort_keys=True, separators=(",", ":"))
        hash_key = f"hybrid_last_snapshot_hash_{ws['id']}"
        id_map_key = f"hybrid_line_id_map_{ws['id']}"
        line_id_map = st.session_state.setdefault(id_map_key, {})
        if st.session_state.get(hash_key) != snapshot_hash:
            st.session_state[hash_key] = snapshot_hash

            snapshot_lines = _normalize_snapshot_lines(overlay_snapshot.get("lines") or [], line_id_map)
            persisted_lines = api.list_lines(ws["id"])
            persisted_by_id = {str(line["id"]): line for line in persisted_lines}
            snapshot_by_id = {str(line.get("id")): line for line in snapshot_lines if line.get("id")}

            for persisted_id in list(persisted_by_id):
                if persisted_id not in snapshot_by_id:
                    persist_line_edit(persisted_id, {"action": "delete"})

            for snap_line in snapshot_lines:
                snap_id = str(snap_line.get("id") or "")
                if snap_id and snap_id in persisted_by_id:
                    patch = _line_patch(persisted_by_id[snap_id], snap_line)
                    if patch:
                        persist_line_edit(snap_id, {"action": "update", "patch": patch})
                    continue

                created = persist_line_edit(
                    "",
                    {
                        "action": "create",
                        "project_id": ws["id"],
                        "line": snap_line,
                    },
                )
                local_id = str(snap_line.get("id") or "")
                server_id = str(created.get("id") or "")
                if local_id and server_id:
                    line_id_map[local_id] = server_id

            refreshed_lines = api.list_lines(ws["id"])
            refreshed_line_ids = [str(line["id"]) for line in refreshed_lines]
            st.session_state[f"hybrid_live_counts_{ws['id']}"] = request_live_counts(
                spec.project_id,
                spec.video_ids,
                refreshed_line_ids,
            )
            # Invalidate any cached export when lines change.
            st.session_state.pop(f"hybrid_xlsx_{ws['id']}", None)

    live_counts = st.session_state.get(f"hybrid_live_counts_{ws['id']}")
    if live_counts:
        st.subheader("Live Counts")
        st.json(live_counts)

    st.subheader("Export")
    line_ids_for_export = [str(l["id"]) for l in lines]
    col_btn, col_dl = st.columns([2, 3])
    with col_btn:
        if st.button(
            "Prepare XLSX Export",
            disabled=not (spec.video_ids and line_ids_for_export),
            help="Compute counts and prepare an Excel workbook for download.",
        ):
            try:
                xlsx_bytes = api.export_xlsx(spec.project_id, spec.video_ids, line_ids_for_export)
                st.session_state[f"hybrid_xlsx_{ws['id']}"] = xlsx_bytes
            except api.APIError as exc:
                st.error(str(exc))
    with col_dl:
        xlsx_bytes = st.session_state.get(f"hybrid_xlsx_{ws['id']}")
        if xlsx_bytes:
            st.download_button(
                "Download XLSX",
                data=xlsx_bytes,
                file_name=f"counts-{ws['name'].replace(' ', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


render_page()
