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
import os
import time
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
    video_id: str
    selected_line_ids: List[str]
    frame_count: int
    active_layers: Tuple[str, ...]


def build_viewport_spec(
    ws: Dict[str, Any],
    video: Dict[str, Any],
    lines: List[Dict[str, Any]],
) -> ViewportSpec:
    """Build the state package consumed by the embedded React/Vite overlay."""
    active_layers = ["saved-lines", "frame-scrubber", "trajectories"]
    if lines:
        active_layers.append("direction-overlay")
    if video.get("status") == "analyzed":
        active_layers.append("counts")

    return ViewportSpec(
        project_id=str(ws["id"]),
        video_id=str(video["id"]),
        selected_line_ids=[str(ln["id"]) for ln in lines],
        frame_count=100,
        active_layers=tuple(dict.fromkeys(active_layers)),
    )


def _absolute_url(rel: Optional[str]) -> Optional[str]:
    if not rel:
        return None
    return api.file_url(rel)


def _browser_api_base_url() -> Optional[str]:
    """Return the API URL the *browser* should use, or None to let the React
    iframe derive it from window.location. ``PUBLIC_API_URL`` defaults to
    ``http://localhost:8000`` on the server — that's correct for laptop dev
    but wrong on a remote VPS, where the user's browser would try to hit
    its own loopback. Strip the localhost default and let the iframe resolve.
    """
    raw = os.environ.get("PUBLIC_API_URL", "").strip()
    if not raw:
        return None
    if "localhost" in raw or "127.0.0.1" in raw:
        return None
    return raw


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


def _render_video_selector(ws_id: str, videos: List[Dict[str, Any]]) -> str:
    """Render the single-video selector and return the chosen video ID.

    Lines now belong to one video at a time, so the page operates on exactly
    one selection. Identity is keyed by video ID (not by label) so re-analysis
    can change the track count without dropping the selection.
    """
    sel_key = f"count_export_selected_video_{ws_id}"
    video_by_id: Dict[str, Dict[str, Any]] = {str(v["id"]): v for v in videos}
    ids = list(video_by_id.keys())

    current = st.session_state.get(sel_key)
    if current not in video_by_id:
        current = ids[0]
        st.session_state[sel_key] = current

    def _label(vid: str) -> str:
        v = video_by_id[vid]
        src = v.get("local_source_path") or ""
        folder = os.path.dirname(src) if src else ""
        prefix = f"{folder}/" if folder else ""
        return f'{prefix}{v["filename"]} · {v.get("num_tracks", 0)} tracks'

    st.selectbox(
        "📼 Video",
        options=ids,
        format_func=_label,
        key=sel_key,
    )
    return st.session_state[sel_key]


# Fragment-scoped reruns keep the export poll out of the full-page
# rerun cycle. The decorator is only available on Streamlit ≥ 1.33;
# on older versions we degrade to a plain function call and a
# whole-page rerun (the previous behaviour).
_fragment = getattr(st, "fragment", None) or getattr(st, "experimental_fragment", None)
if _fragment is not None:
    _export_block_decorator = _fragment
else:
    def _export_block_decorator(fn):
        return fn


@_export_block_decorator
def _export_block(selected_video_id: str, line_ids_for_export: List[str]) -> None:
    """Render the XLSX-export button + status + download.

    On Streamlit ≥ 1.33 this is a fragment, so polling reruns affect
    only this widget — the iframe and its frame URLs are not rebuilt.
    """
    job_key = f"hybrid_export_job_{selected_video_id}"
    file_key = f"hybrid_xlsx_{selected_video_id}"
    name_key = f"hybrid_xlsx_name_{selected_video_id}"

    def _rerun() -> None:
        # st.rerun(scope="fragment") was added alongside st.fragment;
        # try the scoped form first, fall back to whole-page rerun.
        try:
            st.rerun(scope="fragment")
        except TypeError:
            st.rerun()

    col_btn, col_status = st.columns([2, 3])
    with col_btn:
        can_export = bool(selected_video_id and line_ids_for_export)
        if st.button(
            "Prepare XLSX Export",
            disabled=not can_export,
            help="Compute counts and prepare an Excel workbook for download.",
        ):
            try:
                # Re-fetch lines at click time — the React iframe adds/deletes lines
                # directly via FastAPI without triggering a Streamlit page rerun, so
                # line_ids_for_export (built at render time) may be stale.
                live_ids = [str(l["id"]) for l in api.list_lines(selected_video_id)]
                if not live_ids:
                    st.warning("No counting lines on this video — draw lines in the editor first.")
                else:
                    resp = api.start_export(selected_video_id, live_ids)
                    st.session_state[job_key] = resp["job_id"]
                    st.session_state.pop(file_key, None)
                    _rerun()
            except api.APIError as exc:
                st.error(str(exc))

    with col_status:
        job_id = st.session_state.get(job_key)
        if job_id and not st.session_state.get(file_key):
            try:
                status = api.get_export_status(job_id)
            except api.APIError as exc:
                st.error(str(exc))
                st.session_state.pop(job_key, None)
            else:
                if status["status"] in ("pending", "running"):
                    st.info(f"Preparing… ({status['status']})")
                    time.sleep(1.5)
                    _rerun()
                elif status["status"] == "error":
                    st.error(f"Export failed: {status.get('error') or 'unknown error'}")
                    st.session_state.pop(job_key, None)
                elif status["status"] == "done":
                    try:
                        st.session_state[file_key] = api.download_export(job_id)
                        st.session_state[name_key] = status.get("filename", "counts.xlsx")
                    except api.APIError as exc:
                        st.error(str(exc))
                    else:
                        st.success("Ready.")

        xlsx_bytes = st.session_state.get(file_key)
        if xlsx_bytes:
            st.download_button(
                "📥 Download XLSX",
                data=xlsx_bytes,
                file_name=st.session_state.get(name_key) or "counts.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


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
        st.info("No analyzed videos in this workspace. Process videos on the Watched Folder page first.")
        st.stop()

    ws_id = str(ws["id"])

    selected_video_id = _render_video_selector(ws_id, videos)
    preview_video = next(v for v in videos if str(v["id"]) == selected_video_id)

    # Hybrid iframe key includes the video id so React fully remounts when the
    # user picks a different video — different bootstrap, different line set.
    hybrid_key = f"hybrid_viewport_{ws_id}_{selected_video_id}"

    # Streamlit fetches the initial line list for bootstrap only. After mount,
    # the React iframe owns line CRUD and talks to FastAPI directly — see
    # hybrid_viewport/src/App.tsx (server-diff effect) and api.ts.
    lines = api.list_lines(selected_video_id)
    spec = build_viewport_spec(ws, preview_video, lines)

    # Fetch scene-based keyframes. Frame[0] is inlined as a base64 data URI
    # (same approach as trajectories/heatmap) so the background always renders
    # even when PUBLIC_API_URL is unset and the browser can't reach the internal
    # Docker hostname. Remaining scrubber frames stay as direct URLs (capped at
    # MAX_SCENE_FRAMES=30; the browser loads them lazily while scrubbing and
    # degrades silently if PUBLIC_API_URL isn't configured).
    try:
        raw_frames = api.list_video_frames(preview_video["id"])
        frames_for_bootstrap = []
        for i, f in enumerate(raw_frames):
            if i == 0 and f.get("url"):
                url = _fetch_asset_data_url(f["url"]) or api.file_url(f["url"])
            else:
                url = api.file_url(f["url"]) if f.get("url") else None
            frames_for_bootstrap.append({**f, "url": url})
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

    # Live counts are computed entirely browser-side by the iframe — see the
    # mount-time scheduleCountsRefresh() in App.tsx. Pre-fetching them here
    # used to block the page render for minutes on the first cold-cache call
    # for a long video and trip httpx's 120s timeout.
    suggestions_key = f"hybrid_suggestions_{selected_video_id}"
    suggestions = st.session_state.get(suggestions_key)

    # frameCount driven by actual scene count; frameUrl is legacy fallback.
    scene_count = max(len(frames_for_bootstrap), 1)
    legacy_frame_url = frames_for_bootstrap[0]["url"] if frames_for_bootstrap else None

    bootstrap = {
        "spec": {
            "projectId": spec.project_id,
            "videoId": spec.video_id,
            "selectedLineIds": spec.selected_line_ids,
            "frameCount": scene_count,
            "activeLayers": list(spec.active_layers),
        },
        # Browser-reachable base URL so the iframe can call FastAPI directly.
        # Only honor a non-loopback override; otherwise let the iframe derive
        # the host from window.location (remote browsers can't reach
        # "localhost" on the server).
        "apiBaseUrl": _browser_api_base_url(),
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
        "suggestions": suggestions,
    }

    # Render the iframe. React never emits values back — line CRUD and counts
    # refresh go straight to FastAPI. Streamlit reruns are only triggered by
    # workspace/video selection or the XLSX export button below.
    render_hybrid_viewport(bootstrap=bootstrap, key=hybrid_key)

    # Export — async job + fragment-scoped poll. The previous version
    # used a full-page `st.rerun()` every 1.5 s while a job was active,
    # which re-ran the iframe-bootstrap-building code (including the
    # per-frame fetch loop above) on every poll. Wrapping the poll in
    # `st.fragment` confines reruns to the export widget so the rest of
    # the page stays still; it also stops cleanly when the websocket
    # disconnects, so closing the tab no longer leaves a background
    # poll loop hammering the API. Older Streamlit (<1.33) without
    # `st.fragment` falls back to a no-op decorator.
    st.divider()
    st.subheader("Export")
    line_ids_for_export = [str(l["id"]) for l in lines]
    _export_block(selected_video_id, line_ids_for_export)


render_page()
