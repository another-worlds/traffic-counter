import streamlit as st
import api_client as api
from sidebar import render_sidebar

st.set_page_config(page_title="Videos (Legacy)", page_icon="🎥", layout="wide")
st.title("🎥 Videos (Legacy)")

ws = render_sidebar()
if not ws:
    st.warning("Pick or create a workspace in the sidebar to begin.")
    st.stop()

st.warning(
    "Manual uploads are now legacy and frozen. Please use **Watched Folder** for all new video ingestion. "
    "This page is read-only and will be deprecated after migration."
)

videos = api.list_videos(ws["id"])
if not videos:
    st.info("No videos in this workspace.")
    st.stop()

st.subheader("Existing videos (read-only)")
for v in videos:
    with st.container(border=True):
        c1, c2 = st.columns([4, 2])
        with c1:
            st.write(f"**{v['filename']}**")
            st.caption(f"ID: `{v['id'][:8]}…` · created {v['created_at']}")
        with c2:
            st.write(v["status"])
            if v.get("duration_s"):
                st.caption(f"{v['duration_s']:.1f}s")
