"""Hybrid Count & Export page.

Semantic Contract Ref: counting_line_overlay/contract v0.1

The Streamlit shell hosts an embedded React/Vite viewport that owns the live
line editor, frame display, overlay toggles, counts table, auto-suggest,
and direction rose. The API remains the source of truth for persistence
and counting; Streamlit reconciles snapshots and runs API calls.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict, List, Optional, Tuple

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
    active_layers = ["saved-lines", "frame-scrubber", "trajectories"]
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
    """Persist a single line change through the API."""
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
            float(a[0]), float(a[1]),
            float(b[0]), float(b[1]),
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
    """Request live counts. Returns empty summary when there are no active lines."""
    if not line_ids:
        return {"total_unique_tracks": 0, "sum_across_lines": 0, "per_line": []}
    return api.compute_counts(project_id, video_ids, line_ids)


def _counts_for_react(counts_raw: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Reshape API counts payload for the React bootstrap."""
    if not counts_raw:
        return None
    per_line_list = counts_raw.get("per_line") or []
    per_line_map = {str(r["line_id"]): r for r in per_line_list}
    return {
        "total_unique_tracks": int(counts_raw.get("total_unique_tracks") or 0),
        "per_line": per_line_map,
    }


def _points_match(api_line: Dict[str, Any], snap_line: Dict[str, Any]) -> bool:
    points = snap_line.get("points") or []
    if len(points) < 2:
        return False
    a = [float(points[0][0]), float(points[0][1])]
    b = [float(points[-1][0]), float(points[-1][1])]
    current = api_line.get("points") or {}
    ca = current.get("a") or [0, 0]
    cb = current.get("b") or [0, 0]
    tol = 0.5
    return (
        abs(float(ca[0]) - a[0]) <= tol and abs(float(ca[1]) - a[1]) <= tol
        and abs(float(cb[0]) - b[0]) <= tol and abs(float(cb[1]) - b[1]) <= tol
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


def _absolute_url(rel: Optional[str]) -> Optional[str]:
    if not rel:
        return None
    return api.file_url(rel)


def _handle_pending_actions(actions: List[Dict[str, Any]], ws_id: str, spec: ViewportSpec) -> bool:
    """Handle React-emitted side-actions. Returns True if a rerun is needed."""
    if not actions:
        return False
    suggestions_key = f"hybrid_suggestions_{ws_id}"
    changed = False
    for act in actions:
        kind = act.get("type")
        if kind == "request-suggestions":
            n = max(1, min(10, int(act.get("n") or 3)))
            try:
                st.session_state[suggestions_key] = api.suggest_lines(
                    spec.project_id, spec.video_ids, n=n,
                )
                changed = True
            except api.APIError as exc:
                st.error(f"Suggest failed: {exc}")
        elif kind == "dismiss-suggestions":
            st.session_state.pop(suggestions_key, None)
            changed = True
        elif kind == "accept-suggestion":
            sug = act.get("suggestion") or {}
            pts = sug.get("points") or {}
            a = pts.get("a") or [0, 0]
            b = pts.get("b") or [0, 0]
            try:
                api.create_line(
                    spec.project_id,
                    sug.get("name") or "suggested",
                    float(a[0]), float(a[1]),
                    float(b[0]), float(b[1]),
                    color=sug.get("color") or "#4ecdc4",
                )
                # Auto-clear suggestions after accepting one (simpler UX).
                st.session_state.pop(suggestions_key, None)
                changed = True
            except api.APIError as exc:
                st.error(f"Could not add suggestion: {exc}")
    return changed


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

    # Multi-video picker (counts span all selected; preview uses the first)
    video_labels = {f'{v["filename"]} ({v.get("num_tracks", 0)} tracks)': v for v in videos}
    default_picks = [list(video_labels.keys())[0]]
    picks = st.multiselect(
        "Videos to count over",
        options=list(video_labels.keys()),
        default=st.session_state.get("hybrid_selected_video_labels", default_picks),
        help="Counts span all selected videos; the viewport uses the first as preview.",
        key="hybrid_video_multiselect",
    )
    selected_videos = [video_labels[k] for k in picks] if picks else [videos[0]]
    st.session_state["hybrid_selected_video_labels"] = picks or default_picks

    preview_video = selected_videos[0]
    selected_video_ids = [str(v["id"]) for v in selected_videos]
    spec = ViewportSpec(
        project_id=spec.project_id,
        video_ids=selected_video_ids,
        selected_line_ids=spec.selected_line_ids,
        frame_count=spec.frame_count,
        active_layers=spec.active_layers,
    )

    # Fetch scene-based keyframes + overlay asset URLs for the preview video.
    try:
        raw_frames = api.list_video_frames(preview_video["id"])
        frames_for_bootstrap = [
            {**f, "url": _absolute_url(f.get("url"))} for f in raw_frames
        ]
    except Exception:
        frames_for_bootstrap = []

    traj_rel = api.get_trajectories_url(preview_video["id"])
    heatmap_rel: Optional[str] = None
    try:
        heatmap_rel = api.get_heatmap_url(preview_video["id"])
    except Exception:
        heatmap_rel = None

    track_stats: Optional[Dict[str, Any]] = None
    try:
        track_stats = api.track_stats(preview_video["id"])
    except Exception:
        track_stats = None

    # Live counts: prefer cached, fall back to fresh compute.
    counts_key = f"hybrid_live_counts_{ws['id']}"
    if counts_key not in st.session_state and lines:
        st.session_state[counts_key] = request_live_counts(
            spec.project_id, selected_video_ids, [str(l["id"]) for l in lines],
        )
    live_counts_raw = st.session_state.get(counts_key)

    suggestions_key = f"hybrid_suggestions_{ws['id']}"
    suggestions = st.session_state.get(suggestions_key)

    # frameCount driven by actual scene count; frameUrl is legacy fallback.
    scene_count = max(len(frames_for_bootstrap), 1)
    legacy_frame_url = frames_for_bootstrap[0]["url"] if frames_for_bootstrap else None

    bootstrap = {
        "spec": {
            "projectId": spec.project_id,
            "videoIds": spec.video_ids,
            "selectedLineIds": spec.selected_line_ids,
            "frameCount": scene_count,
            "activeLayers": list(spec.active_layers),
        },
        "initialLines": lines,
        "frames": frames_for_bootstrap,
        "frameUrl": legacy_frame_url,
        "trajectoriesUrl": _absolute_url(traj_rel),
        "heatmapUrl": _absolute_url(heatmap_rel),
        "videoSize": {
            "width": int(preview_video.get("width") or 1920),
            "height": int(preview_video.get("height") or 1080),
        },
        "trackStats": track_stats,
        "counts": _counts_for_react(live_counts_raw),
        "suggestions": suggestions,
    }

    overlay_snapshot = render_hybrid_viewport(bootstrap=bootstrap, key=f"hybrid_viewport_{ws['id']}")

    if overlay_snapshot and str(overlay_snapshot.get("projectId") or "") != spec.project_id:
        overlay_snapshot = None

    needs_rerun = False

    if overlay_snapshot:
        # Handle React-emitted side-actions FIRST so that suggestions land before
        # the next snapshot/persistence pass.
        pending_actions = overlay_snapshot.get("pendingActions") or []
        if _handle_pending_actions(pending_actions, str(ws["id"]), spec):
            needs_rerun = True

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

            mutated = False

            # Deletions: any persisted line missing from snapshot.
            for persisted_id in list(persisted_by_id):
                if persisted_id not in snapshot_by_id:
                    try:
                        persist_line_edit(persisted_id, {"action": "delete"})
                        mutated = True
                    except Exception as exc:
                        st.error(f"Delete failed: {exc}")

            # Updates and creates.
            for snap_line in snapshot_lines:
                snap_id = str(snap_line.get("id") or "")
                if snap_id and snap_id in persisted_by_id:
                    patch = _line_patch(persisted_by_id[snap_id], snap_line)
                    if patch:
                        try:
                            persist_line_edit(snap_id, {"action": "update", "patch": patch})
                            mutated = True
                        except Exception as exc:
                            st.error(f"Update failed: {exc}")
                    continue

                try:
                    created = persist_line_edit(
                        "",
                        {"action": "create", "project_id": ws["id"], "line": snap_line},
                    )
                    mutated = True
                    local_id = str(snap_line.get("id") or "")
                    server_id = str(created.get("id") or "")
                    if local_id and server_id:
                        line_id_map[local_id] = server_id
                except Exception as exc:
                    st.error(f"Create failed: {exc}")

            if mutated:
                refreshed_lines = api.list_lines(ws["id"])
                refreshed_line_ids = [str(line["id"]) for line in refreshed_lines]
                st.session_state[counts_key] = request_live_counts(
                    spec.project_id, selected_video_ids, refreshed_line_ids,
                )
                # Invalidate any cached export when lines change.
                st.session_state.pop(f"hybrid_xlsx_{ws['id']}", None)
                needs_rerun = True

    if needs_rerun:
        st.rerun()

    # Export section (Streamlit-side since file download must come from the host).
    st.divider()
    st.subheader("Export")
    line_ids_for_export = [str(l["id"]) for l in lines]
    col_btn, col_dl = st.columns([2, 3])
    with col_btn:
        if st.button(
            "Prepare XLSX Export",
            disabled=not (selected_video_ids and line_ids_for_export),
            help="Compute counts and prepare an Excel workbook for download.",
        ):
            try:
                xlsx_bytes = api.export_xlsx(spec.project_id, selected_video_ids, line_ids_for_export)
                st.session_state[f"hybrid_xlsx_{ws['id']}"] = xlsx_bytes
            except api.APIError as exc:
                st.error(str(exc))
    with col_dl:
        xlsx_bytes = st.session_state.get(f"hybrid_xlsx_{ws['id']}")
        if xlsx_bytes:
            st.download_button(
                "📥 Download XLSX",
                data=xlsx_bytes,
                file_name=f"counts-{ws['name'].replace(' ', '_')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


render_page()
