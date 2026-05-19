"""Hybrid Count & Export page.

Semantic Contract Ref: counting_line_overlay/contract v0.1

The Streamlit shell hosts an embedded React/Vite viewport that owns the live
line editor, frame display, overlay toggles, counts table, auto-suggest, and
direction rose. The React iframe talks to the FastAPI directly for all line
CRUD, counts, and suggestion calls — Streamlit only builds the initial
bootstrap and renders the export download.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
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


def render_page() -> None:
    """Render the hybrid count-and-export page."""
    st.set_page_config(page_title="Count & Export (Hybrid)", page_icon="📏", layout="wide")
    st.title("📏 Count & Export (Hybrid)")

    ws = render_sidebar()
    if not ws:
        st.stop()

    st.caption(f"Workspace: **{ws['name']}**")

    videos = [v for v in api.list_videos(ws["id"]) if v.get("status") == "analyzed"]

    if not videos:
        st.info("No analyzed videos in this workspace. Analyze videos on the Videos page first.")
        st.stop()

    ws_id = str(ws["id"])
    counts_key = f"hybrid_live_counts_{ws_id}"

    selected_video_ids = _render_video_selector(ws_id, videos)
    selected_videos = [v for v in videos if str(v["id"]) in selected_video_ids] or [videos[0]]
    preview_video = selected_videos[0]
    selected_video_ids = [str(v["id"]) for v in selected_videos]

    hybrid_key = f"hybrid_viewport_{ws_id}"

    # Streamlit fetches the initial line list for bootstrap only. After mount,
    # the React iframe owns line CRUD and talks to FastAPI directly — see
    # hybrid_viewport/src/App.tsx (server-diff effect) and api.ts.
    lines = api.list_lines(ws_id)
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
        raw_frames = api.list_video_frames(preview_video["id"])
        frames_for_bootstrap = [
            {**f, "url": _maybe_fetch_asset_data_url(f.get("url"))} for f in raw_frames
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
        # Browser-reachable base URL so the iframe can call FastAPI directly.
        "apiBaseUrl": api.PUBLIC_API_URL,
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

    # Render the iframe. React never emits values back — line CRUD and counts
    # refresh go straight to FastAPI. Streamlit reruns are only triggered by
    # workspace/video selection or the XLSX export button below.
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
