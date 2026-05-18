import streamlit as st
import time
import api_client as api
from sidebar import render_sidebar
from hybrid_viewport.streamlit_bridge import render_uploader

st.set_page_config(page_title="Videos", page_icon="🎥", layout="wide")
st.title("🎥 Videos")

ws = render_sidebar()
if not ws:
    st.warning("Pick or create a workspace in the sidebar to begin.")
    st.stop()

st.caption(f"Workspace: **{ws['name']}**")

# ── global queue controls (same primitives as the Watched Folder page) ─────
try:
    pause_state = api.worker_pause_state()
    err_summary = api.worker_error_summary()
except api.APIError:
    pause_state = {"paused": False}
    err_summary = {"total": 0, "by_source": {}}

upload_errors = err_summary.get("by_source", {}).get("upload", 0)
paused = pause_state.get("paused", False)

ctl_pause, ctl_retry, _ = st.columns([2, 2, 6])
with ctl_pause:
    if paused:
        if st.button("▶ Resume processing", type="primary", key="ctl_resume"):
            try:
                api.resume_worker()
                st.rerun()
            except api.APIError as exc:
                st.error(str(exc))
    else:
        if st.button("⏸ Pause processing", key="ctl_pause",
                     help="Current video finishes; no new claims until resumed."):
            try:
                api.pause_worker()
                st.rerun()
            except api.APIError as exc:
                st.error(str(exc))

with ctl_retry:
    if st.button(f"🔄 Retry all errors ({upload_errors})",
                 disabled=upload_errors == 0,
                 key="ctl_retry_upload",
                 help="Re-queue all upload-source videos in error state."):
        try:
            result = api.retry_all_errors(source="upload")
            st.success(f"Re-queued {result['queued']} video(s).")
            st.rerun()
        except api.APIError as exc:
            st.error(str(exc))

if paused:
    st.warning("⏸ Processing is paused. Click **▶ Resume processing** to continue.")

# Upload
with st.expander("Upload video", expanded=True):
    prog_state = st.session_state.get(f"upload_progress_{ws['id']}", {"pct": 0, "text": "Ready to upload"})
    progress_bar = st.progress(prog_state["pct"], text=prog_state["text"])

    result = render_uploader(
        project_id=ws["id"],
        tus_endpoint=f"{api.PUBLIC_API_URL}/tus/files",
        key=f"tus_uploader_{ws['id']}",
    )

    if result:
        kind = result.get("kind")
        if kind == "progress":
            uploaded = result.get("uploaded", 0)
            total = result.get("total", 1)
            pct = result.get("pct", 0)
            if total > 100_000_000:
                text = f"{uploaded/1e9:.2f} GB / {total/1e9:.2f} GB"
            else:
                text = f"{uploaded/1e6:.1f} MB / {total/1e6:.1f} MB"
            st.session_state[f"upload_progress_{ws['id']}"] = {"pct": pct, "text": text}
            progress_bar.progress(pct, text=text)
        elif kind == "complete":
            st.session_state.pop(f"upload_progress_{ws['id']}", None)
            st.success(f"✅ Uploaded {result.get('filename', 'video')}")
            st.rerun()
        elif kind == "error":
            st.session_state.pop(f"upload_progress_{ws['id']}", None)
            st.error(f"Upload failed: {result.get('message', 'Unknown error')}")

# List
videos = api.list_videos(ws["id"])
if not videos:
    st.info("No videos yet. Upload one above.")
    st.stop()

st.subheader("Videos in this workspace")
auto_refresh = st.checkbox("Auto-refresh status (3s)", value=True,
                           help="Useful while videos are queued or being analyzed.")

for v in videos:
    with st.container(border=True):
        c1, c2, c3 = st.columns([3, 2, 2])
        with c1:
            st.write(f"**{v['filename']}**")
            st.caption(f"ID: `{v['id'][:8]}…` · created {v['created_at']}")
        with c2:
            status = v["status"]
            badge = {
                "uploading": "🟦 uploading",
                "uploaded": "🟦 uploaded",
                "queued": "🟨 queued",
                "analyzing": "🟧 analyzing",
                "analyzed": "🟩 analyzed",
                "error": "🟥 error",
            }.get(status, status)
            st.write(badge)
            if status == "analyzing" and v.get("progress_pct") is not None:
                st.progress(v["progress_pct"], text=f"{v['progress_pct']*100:.0f}%")
            if v.get("num_tracks") is not None and status == "analyzed":
                st.caption(f"{v['num_tracks']} tracks · "
                           f"{(v['duration_s'] or 0):.1f}s · "
                           f"{v.get('width')}×{v.get('height')} @ {(v.get('fps') or 0):.1f}fps")
            if status == "error" and v.get("error_message"):
                with st.expander("Error details"):
                    st.code(v["error_message"])
        with c3:
            if status in ("uploaded", "error"):
                if st.button("Analyze", key=f"analyze_{v['id']}"):
                    api.analyze_video(v["id"])
                    st.rerun()
            elif status == "analyzed":
                if st.button("Re-analyze", key=f"reanalyze_{v['id']}"):
                    api.analyze_video(v["id"])
                    st.rerun()
            if st.button("Delete", key=f"del_{v['id']}"):
                api.delete_video(v["id"])
                st.rerun()

if auto_refresh and any(v["status"] in ("uploading", "queued", "analyzing") for v in videos):
    time.sleep(3)
    st.rerun()
