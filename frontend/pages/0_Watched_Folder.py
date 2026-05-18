"""Yandex Disk auto-import: shows videos discovered by the watcher service."""
import os
import time

import streamlit as st

import api_client as api

st.set_page_config(page_title="Watched Folder", page_icon="📂", layout="wide")
st.title("📂 Watched Folder")

WATCH_PATH = os.environ.get("WATCHER_WATCH_PATH", "/mnt/yandex-videos")
st.caption(
    f"Watched path: `{WATCH_PATH}` · "
    "The watcher service registers videos automatically as they land in that folder."
)

# ── fetch data ───────────────────────────────────────────────────────────────
try:
    videos = api.list_local_folder_videos()
except api.APIError as exc:
    st.error(f"Could not reach API: {exc}")
    st.stop()

# ── summary stats ─────────────────────────────────────────────────────────────
total      = len(videos)
analyzed   = sum(1 for v in videos if v["status"] == "analyzed")
in_queue   = sum(1 for v in videos if v["status"] in ("queued", "analyzing"))
errors     = sum(1 for v in videos if v["status"] == "error")
unstarted  = sum(1 for v in videos if v["status"] == "uploaded")

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Total indexed", total)
col2.metric("Analyzed", analyzed)
col3.metric("In queue / running", in_queue)
col4.metric("Errors", errors)
col5.metric("Not yet queued", unstarted)

# ── actions ───────────────────────────────────────────────────────────────────
st.divider()

btn_col, info_col = st.columns([2, 5])
with btn_col:
    if st.button("▶ Analyze all unprocessed", disabled=unstarted == 0,
                 help="Queue every video that has not been analyzed yet."):
        try:
            result = api.analyze_pending_local_folder()
            st.success(f"Queued {result['queued']} video(s) for analysis.")
            st.rerun()
        except api.APIError as exc:
            st.error(str(exc))

with info_col:
    auto_refresh = st.checkbox(
        "Auto-refresh (3 s)",
        value=in_queue > 0,
        help="Useful while videos are being analyzed.",
    )

# ── video table ───────────────────────────────────────────────────────────────
if not videos:
    st.info(
        "No videos indexed yet. "
        "The watcher service will register them automatically once files appear in "
        f"`{WATCH_PATH}`."
    )
    st.stop()

st.subheader("Indexed videos")

STATUS_COLORS = {
    "uploaded":  "🔵",
    "queued":    "🟡",
    "analyzing": "🟠",
    "analyzed":  "🟢",
    "error":     "🔴",
}

for v in videos:
    icon = STATUS_COLORS.get(v["status"], "⚪")
    label = f"{icon} **{v['filename']}** — {v['status']}"
    if v["status"] == "analyzing" and v.get("progress_pct") is not None:
        pct = v["progress_pct"]
        label += f" ({pct*100:.0f}%)"

    with st.container(border=True):
        c1, c2, c3 = st.columns([4, 3, 2])
        with c1:
            st.markdown(label)
            if v.get("local_source_path"):
                st.caption(v["local_source_path"])
        with c2:
            if v["status"] == "analyzed":
                parts = []
                if v.get("duration_s"):
                    parts.append(f"{v['duration_s']:.1f}s")
                if v.get("num_tracks"):
                    parts.append(f"{v['num_tracks']} tracks")
                if v.get("width") and v.get("height"):
                    parts.append(f"{v['width']}×{v['height']}")
                st.caption("  ·  ".join(parts))
            elif v["status"] == "error" and v.get("error_message"):
                st.caption(v["error_message"][:120])
            elif v.get("size_bytes"):
                size = v["size_bytes"]
                st.caption(f"{size/1e9:.2f} GB" if size > 1e9 else f"{size/1e6:.1f} MB")
        with c3:
            if v["status"] == "uploaded":
                if st.button("Analyze", key=f"analyze_{v['id']}"):
                    try:
                        api.analyze_video(v["id"])
                        st.rerun()
                    except api.APIError as exc:
                        st.error(str(exc))
            elif v["status"] == "analyzing" and v.get("progress_pct") is not None:
                st.progress(v["progress_pct"])

# ── auto-refresh ──────────────────────────────────────────────────────────────
if auto_refresh and in_queue > 0:
    time.sleep(3)
    st.rerun()
