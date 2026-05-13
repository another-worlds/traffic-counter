import streamlit as st
import time
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
    file = st.file_uploader("Video file (mp4, mov, mkv)", type=["mp4", "mov", "mkv", "avi"])
    if file is not None and st.button("Upload"):
        with st.spinner("Uploading..."):
            try:
                v = api.upload_video(ws["id"], file.name, file.getvalue())
                st.success(f"Uploaded {file.name}")
                st.session_state["just_uploaded_id"] = v["id"]
                st.rerun()
            except Exception as e:
                st.error(str(e))

# ── Yandex.Disk browser ────────────────────────────────────────────────────────
with st.expander("📂 Import from Yandex.Disk", expanded=False):
    # Check if the feature is available
    try:
        _yd_probe = api.browse_disk("")
        _yd_available = True
    except api.APIError as _e:
        _yd_available = False
        st.info(f"Yandex.Disk is not configured on the server: {_e}")

    if _yd_available:
        # Keep browsing path in session state
        if "yd_path" not in st.session_state:
            st.session_state["yd_path"] = ""

        # ── breadcrumb navigation ──────────────────────────────────────────────
        parts = [p for p in st.session_state["yd_path"].split("/") if p]
        crumb_cols = st.columns(len(parts) + 1, gap="small")
        with crumb_cols[0]:
            if st.button("🏠 root", key="yd_crumb_root"):
                st.session_state["yd_path"] = ""
                st.rerun()
        for i, part in enumerate(parts):
            with crumb_cols[i + 1]:
                label = f"/ {part}"
                if st.button(label, key=f"yd_crumb_{i}"):
                    st.session_state["yd_path"] = "/".join(parts[: i + 1])
                    st.rerun()

        # ── directory listing ──────────────────────────────────────────────────
        try:
            listing = api.browse_disk(st.session_state["yd_path"])
        except api.APIError as e:
            st.error(str(e))
            listing = None

        if listing:
            entries = listing["entries"]
            if not entries:
                st.caption("Empty folder.")
            else:
                for entry in entries:
                    col_icon, col_name, col_meta, col_action = st.columns(
                        [0.04, 0.55, 0.25, 0.16], gap="small"
                    )
                    with col_icon:
                        st.write("📁" if entry["is_dir"] else ("🎬" if entry["is_video"] else "📄"))
                    with col_name:
                        if entry["is_dir"]:
                            if st.button(
                                entry["name"],
                                key=f"yd_dir_{entry['name']}",
                                use_container_width=True,
                            ):
                                base = st.session_state["yd_path"]
                                st.session_state["yd_path"] = (
                                    f"{base}/{entry['name']}".lstrip("/")
                                )
                                st.rerun()
                        else:
                            st.write(entry["name"])
                    with col_meta:
                        if entry.get("size") is not None:
                            mb = entry["size"] / 1_048_576
                            st.caption(f"{mb:.1f} MB")
                    with col_action:
                        if entry["is_video"]:
                            btn_key = f"yd_import_{listing['path']}/{entry['name']}"
                            if st.button("Import", key=btn_key, use_container_width=True):
                                disk_path = (
                                    f"{listing['path']}/{entry['name']}".lstrip("/")
                                )
                                with st.spinner(f"Importing {entry['name']}…"):
                                    try:
                                        v = api.import_from_disk(ws["id"], disk_path)
                                        st.success(f"Imported **{v['filename']}**")
                                        st.session_state["just_uploaded_id"] = v["id"]
                                        st.rerun()
                                    except api.APIError as e:
                                        st.error(str(e))

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

