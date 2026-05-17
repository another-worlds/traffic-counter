import streamlit as st
import time
import httpx
import api_client as api
from sidebar import render_sidebar

st.set_page_config(page_title="Videos", page_icon="🎥", layout="wide")
st.title("🎥 Videos")

ws = render_sidebar()
if not ws:
    st.warning("Pick or create a workspace in the sidebar to begin.")
    st.stop()

st.caption(f"Workspace: **{ws['name']}**")

# Upload
with st.expander("Upload video", expanded=True):
    file = st.file_uploader("Video file (mp4, mov, mkv, up to 100GB)", type=["mp4", "mov", "mkv", "avi"])
    if file is not None:
        file_size_mb = file.size / (1024 * 1024)
        file_size_gb = file_size_mb / 1024
        st.caption(f"File size: {file_size_gb:.1f} GB" if file_size_mb > 1024 else f"File size: {file_size_mb:.1f} MB")

        if st.button("Upload"):
            try:
                # For files >1GB, stream from file.file to avoid memory exhaustion
                if file_size_mb > 1024:
                    st.warning(
                        f"Large file ({file_size_gb:.1f} GB) — streaming upload. "
                        "This may take several minutes depending on network speed. "
                        "**Do not close this page** until complete."
                    )
                    file.file.seek(0)
                    data = file.file
                    with st.spinner(f"Uploading {file.name} ({file_size_gb:.1f} GB)... This may take a few minutes."):
                        v = api.upload_video(ws["id"], file.name, data)
                else:
                    with st.spinner("Uploading..."):
                        data = file.getvalue()
                        v = api.upload_video(ws["id"], file.name, data)

                st.success(f"✅ Successfully uploaded {file.name}")
                st.session_state["just_uploaded_id"] = v["id"]
                st.rerun()
            except httpx.TimeoutException:
                st.error(
                    "⏱️ Upload timed out. The file may be too large for your network connection. "
                    "Try uploading from a faster connection or split the file."
                )
            except httpx.ConnectError:
                st.error("❌ Connection failed. Is the API server running? Check the API logs.")
            except Exception as e:
                st.error(f"❌ Upload failed: {str(e)}")

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

if auto_refresh and any(v["status"] in ("queued", "analyzing") for v in videos):
    time.sleep(3)
    st.rerun()

