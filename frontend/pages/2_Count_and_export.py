"""Hybrid Count & Export page.

Semantic Contract Ref: counting_line_overlay/contract v0.1

The Streamlit shell hosts an embedded React/Vite viewport that owns the live
line editor, frame display, overlay toggles, counts table, auto-suggest,
and direction rose. The API remains the source of truth for persistence
and counting; Streamlit reconciles snapshots and runs API calls.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import json
from typing import Any, Dict, List, Optional, Tuple

import httpx
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


@st.cache_data(show_spinner=False, max_entries=64)
def _fetch_asset_data_url(rel_url: str) -> Optional[str]:
    """Fetch an API asset over the internal Docker network and return it as a
    ``data:`` URI so the iframe never needs browser-to-API connectivity.

    PUBLIC_API_URL (used by ``api.file_url``) is only reachable from the user's
    browser in localhost-style deployments. For SSH-tunnel, reverse-proxy, or
    Cloud Run setups it silently fails, which is why the React viewport was
    showing the "No preview frame" fallback for every video. Embedding the
    bytes ships them through the Streamlit component bridge instead.
    """
    try:
        with httpx.Client(base_url=api.API_URL, timeout=15.0) as c:
            r = c.get(rel_url)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    content_type = r.headers.get("content-type", "image/jpeg")
    b64 = base64.b64encode(r.content).decode("ascii")
    return f"data:{content_type};base64,{b64}"


def _maybe_fetch_asset_data_url(rel_url: Optional[str]) -> Optional[str]:
    return _fetch_asset_data_url(rel_url) if rel_url else None


@st.cache_data(show_spinner=False, ttl=5, max_entries=64)
def _cached_list_videos(project_id: str) -> List[Dict[str, Any]]:
    return api.list_videos(project_id)


@st.cache_data(show_spinner=False, ttl=3, max_entries=64)
def _cached_list_lines(project_id: str) -> List[Dict[str, Any]]:
    return api.list_lines(project_id)


@st.cache_data(show_spinner=False, ttl=30, max_entries=128)
def _cached_preview_assets(video_id: str) -> Dict[str, Any]:
    frames = api.list_video_frames(video_id)
    traj_rel = api.get_trajectories_url(video_id)
    try:
        heatmap_rel = api.get_heatmap_url(video_id)
    except Exception:
        heatmap_rel = None
    try:
        stats = api.track_stats(video_id)
    except Exception:
        stats = None
    return {
        "frames": frames,
        "trajectories_rel": traj_rel,
        "heatmap_rel": heatmap_rel,
        "track_stats": stats,
    }


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
            # React adds the line locally; reconciliation creates it in the DB.
            # We only need to clear the suggestion cache here.
            st.session_state.pop(suggestions_key, None)
            changed = True
    return changed


def _render_video_selector(ws_id: str, videos: List[Dict[str, Any]]) -> List[str]:
    """Render the multi-video selector and return the list of selected video IDs.

    Identity is keyed by video ID (not by label string), so re-analysis can
    change the track count without dropping the selection. The selection
    lives in a single session_state key and is reconciled against the
    current video list on every rerun.
    """
    sel_key = f"count_export_selected_videos_{ws_id}"
    video_by_id: Dict[str, Dict[str, Any]] = {str(v["id"]): v for v in videos}

    if sel_key not in st.session_state:
        st.session_state[sel_key] = [str(videos[0]["id"])] if videos else []
    else:
        current_ids = set(video_by_id.keys())
        st.session_state[sel_key] = [
            vid for vid in st.session_state[sel_key] if vid in current_ids
        ]
        if not st.session_state[sel_key] and videos:
            st.session_state[sel_key] = [str(videos[0]["id"])]

    selected_ids: List[str] = list(st.session_state[sel_key])

    def _label(vid: str) -> str:
        v = video_by_id[vid]
        return f'{v["filename"]} · {v.get("num_tracks", 0)} tracks'

    with st.expander("📼 Videos in this count", expanded=True):
        can_remove = len(selected_ids) > 1
        for vid in selected_ids:
            row = st.columns([0.06, 0.84, 0.10])
            with row[0]:
                if st.button(
                    "✕",
                    key=f"_remove_video_{ws_id}_{vid}",
                    help=(
                        "Remove from selection" if can_remove
                        else "At least one video must remain selected"
                    ),
                    disabled=not can_remove,
                ):
                    st.session_state[sel_key] = [
                        v for v in st.session_state[sel_key] if v != vid
                    ]
                    st.rerun()
            with row[1]:
                st.write(_label(vid))
            with row[2]:
                if vid == selected_ids[0]:
                    st.caption("preview source")

        unselected = [vid for vid in video_by_id if vid not in selected_ids]
        picker_key = f"_add_video_picker_{ws_id}"
        if unselected:
            options = [""] + unselected
            # Drop a stale picker value (the previously-picked video is now
            # selected and has dropped out of options). This MUST happen
            # before the selectbox is instantiated this run.
            if (
                st.session_state.get(picker_key)
                and st.session_state[picker_key] not in options
            ):
                st.session_state[picker_key] = ""

            def _on_pick() -> None:
                picked = st.session_state.get(picker_key) or ""
                if picked and picked not in st.session_state[sel_key]:
                    st.session_state[sel_key] = st.session_state[sel_key] + [picked]

            st.selectbox(
                "Add another video",
                options=options,
                format_func=lambda v: "➕ Add another video…" if v == "" else _label(v),
                key=picker_key,
                on_change=_on_pick,
                label_visibility="collapsed",
            )

        if selected_ids:
            preview_name = video_by_id[selected_ids[0]]["filename"]
            st.caption(
                f"Counting over **{len(selected_ids)}** video(s). "
                f"Preview frame and overlays from **{preview_name}**."
            )

    return list(st.session_state[sel_key])


def _process_snapshot(
    overlay_snapshot: Dict[str, Any],
    ws_id: str,
    selected_video_ids: List[str],
    counts_key: str,
) -> None:
    """Process a React snapshot: handle side-actions and persist line edits.

    Runs BEFORE the bootstrap is rebuilt so that the lines list fetched
    afterwards already reflects any newly-created lines. Without this
    ordering the React-side `replace-lines` reconciliation drops the
    locally-drawn line before its POST has been issued.
    """
    if str(overlay_snapshot.get("projectId") or "") != ws_id:
        return

    snapshot_lines = overlay_snapshot.get("lines") or []
    pending_actions = overlay_snapshot.get("pendingActions") or []
    semantic_payload = {"lines": snapshot_lines, "pendingActions": pending_actions}
    semantic_hash = json.dumps(semantic_payload, sort_keys=True, separators=(",", ":"))
    hash_key = f"hybrid_last_semantic_snapshot_hash_{ws_id}"
    if st.session_state.get(hash_key) == semantic_hash:
        # Only pointer/viewport UI state changed; skip API sync work.
        return
    st.session_state[hash_key] = semantic_hash

    snap_spec = ViewportSpec(
        project_id=ws_id,
        video_ids=selected_video_ids,
        selected_line_ids=[],
        frame_count=1,
        active_layers=(),
    )
    _handle_pending_actions(pending_actions, ws_id, snap_spec)

    id_map_key = f"hybrid_line_id_map_{ws_id}"
    line_id_map = st.session_state.setdefault(id_map_key, {})
    snapshot_lines = _normalize_snapshot_lines(snapshot_lines, line_id_map)
    persisted_lines = _cached_list_lines(ws_id)
    persisted_by_id = {str(line["id"]): line for line in persisted_lines}
    snapshot_by_id = {str(line.get("id")): line for line in snapshot_lines if line.get("id")}

    mutated = False

    for persisted_id in list(persisted_by_id):
        if persisted_id not in snapshot_by_id:
            try:
                persist_line_edit(persisted_id, {"action": "delete"})
                mutated = True
            except Exception as exc:
                st.error(f"Delete failed: {exc}")

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
                {"action": "create", "project_id": ws_id, "line": snap_line},
            )
            mutated = True
            local_id = str(snap_line.get("id") or "")
            server_id = str(created.get("id") or "")
            if local_id and server_id:
                line_id_map[local_id] = server_id
        except Exception as exc:
            st.error(f"Create failed: {exc}")

    if mutated:
        _cached_list_lines.clear()
        refreshed_lines = _cached_list_lines(ws_id)
        refreshed_line_ids = [str(line["id"]) for line in refreshed_lines]
        st.session_state[counts_key] = request_live_counts(
            ws_id, selected_video_ids, refreshed_line_ids,
        )
        st.session_state.pop(f"hybrid_xlsx_{ws_id}", None)


def render_page() -> None:
    """Render the hybrid count-and-export page."""
    st.set_page_config(page_title="Count & Export (Hybrid)", page_icon="📏", layout="wide")
    st.title("📏 Count & Export (Hybrid)")

    ws = render_sidebar()
    if not ws:
        st.stop()

    st.caption(f"Workspace: **{ws['name']}**")

    videos = [v for v in _cached_list_videos(ws["id"]) if v.get("status") == "analyzed"]

    if not videos:
        st.info("No analyzed videos in this workspace. Analyze videos on the Videos page first.")
        st.stop()

    ws_id = str(ws["id"])
    counts_key = f"hybrid_live_counts_{ws_id}"

    # Render the video selector first — _process_snapshot needs the current
    # selection so pending suggest-lines actions hit the right video set.
    selected_video_ids = _render_video_selector(ws_id, videos)
    selected_videos = [v for v in videos if str(v["id"]) in selected_video_ids] or [videos[0]]
    preview_video = selected_videos[0]
    selected_video_ids = [str(v["id"]) for v in selected_videos]

    # Process the previous rerun's React snapshot BEFORE fetching the lines
    # list. Otherwise the bootstrap is built with stale lines (no new line
    # yet), and React's `replace-lines` reconciliation throws away the
    # locally-drawn line as soon as the iframe re-renders.
    hybrid_key = f"hybrid_viewport_{ws_id}"
    pending_snapshot = st.session_state.get(hybrid_key)
    if pending_snapshot:
        _process_snapshot(pending_snapshot, ws_id, selected_video_ids, counts_key)

    # Now fetch the lines, which include any just-persisted line.
    lines = _cached_list_lines(ws_id)
    spec = build_viewport_spec(ws, videos, lines)
    spec = ViewportSpec(
        project_id=spec.project_id,
        video_ids=selected_video_ids,
        selected_line_ids=spec.selected_line_ids,
        frame_count=spec.frame_count,
        active_layers=spec.active_layers,
    )

    # Fetch scene-based keyframes + overlay asset URLs for the preview video.
    # Bytes are inlined as data: URIs so the browser never has to reach the API
    # directly — see _fetch_asset_data_url for the why.
    try:
        preview_assets = _cached_preview_assets(preview_video["id"])
    except Exception:
        preview_assets = {"frames": [], "trajectories_rel": None, "heatmap_rel": None, "track_stats": None}
    raw_frames = preview_assets["frames"]
    frames_for_bootstrap = [
        {**f, "url": _maybe_fetch_asset_data_url(f.get("url"))} for f in raw_frames
    ]
    traj_rel = preview_assets["trajectories_rel"]
    heatmap_rel = preview_assets["heatmap_rel"]
    track_stats = preview_assets["track_stats"]

    # Live counts: prefer cached, fall back to fresh compute.
    if counts_key not in st.session_state and lines:
        st.session_state[counts_key] = request_live_counts(
            spec.project_id, selected_video_ids, [str(l["id"]) for l in lines],
        )
    live_counts_raw = st.session_state.get(counts_key)

    suggestions_key = f"hybrid_suggestions_{ws_id}"
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
        "trajectoriesUrl": _maybe_fetch_asset_data_url(traj_rel),
        "heatmapUrl": _maybe_fetch_asset_data_url(heatmap_rel),
        "videoSize": {
            "width": int(preview_video.get("width") or 1920),
            "height": int(preview_video.get("height") or 1080),
        },
        "trackStats": track_stats,
        "counts": _counts_for_react(live_counts_raw),
        "suggestions": suggestions,
    }

    # Render the iframe. The returned value is stashed in st.session_state[hybrid_key]
    # and will be picked up by _process_snapshot at the top of the NEXT rerun.
    render_hybrid_viewport(bootstrap=bootstrap, key=hybrid_key)

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
