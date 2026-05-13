import streamlit as st
import time
import api_client as api

st.set_page_config(page_title="Videos", page_icon="🎥", layout="wide")
st.title("🎥 Videos")

proj = st.session_state.get("project")
if not proj:
    st.warning("Pick a project in the sidebar on the main page first.")
    st.stop()

st.caption(f"Project: **{proj['name']}**")

# Upload
with st.expander("Upload video", expanded=True):
    file = st.file_uploader("Video file (mp4, mov, mkv)", type=["mp4", "mov", "mkv", "avi"])
    if file is not None and st.button("Upload"):
        with st.spinner("Uploading..."):
            try:
                v = api.upload_video(proj["id"], file.name, file.getvalue())
                st.success(f"Uploaded {file.name}")
                st.session_state["just_uploaded_id"] = v["id"]
                st.rerun()
            except Exception as e:
                st.error(str(e))

# List
videos = api.list_videos(proj["id"])
if not videos:
    st.info("No videos yet. Upload one above.")
    st.stop()

st.subheader("Videos in this project")
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
