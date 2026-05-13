import time
from datetime import datetime

import streamlit as st
import api_client as api
from sidebar import render_sidebar, _workspace_status, _fmt_duration, _fmt_size

st.set_page_config(page_title="Traffic Counter", page_icon="🚗", layout="wide")

render_sidebar()

st.title("🚗 Traffic Counter")

# =============================================================================
# Section A — Worker Activity
# =============================================================================
st.subheader("⚙️ Worker activity")

try:
    active_jobs = api.worker_status()
except Exception as e:
    st.warning(f"Could not reach worker status: {e}")
    active_jobs = []

if not active_jobs:
    st.success("Worker is idle — no videos currently queued or analyzing.")
else:
    now = datetime.utcnow()
    total_jobs = len(active_jobs)
    overall_pct = sum(j.get("progress_pct", 0) for j in active_jobs) / total_jobs

    for job in active_jobs:
        pct = job.get("progress_pct") or 0.0
        status = job["status"]
        filename = job["filename"]
        ws_name = job["project_name"]

        started_raw = job.get("started_analyzing_at")
        eta_str = ""
        if started_raw and pct > 0.05:
            try:
                started = datetime.fromisoformat(started_raw.replace("Z", ""))
                elapsed_s = (now - started).total_seconds()
                remaining_s = elapsed_s / pct * (1.0 - pct)
                mins = int(remaining_s // 60)
                secs = int(remaining_s % 60)
                eta_str = f" · ~{mins}m {secs:02d}s left" if mins else f" · ~{secs}s left"
            except Exception:
                pass

        label = f"**{filename}** — {ws_name}"
        if status == "queued":
            st.write(f"🟨 {label} — *queued*")
        else:
            with st.container():
                st.write(f"🟧 {label}{eta_str}")
                st.progress(pct, text=f"{pct*100:.0f}%")

    st.caption(f"{total_jobs} job(s) in progress · overall {overall_pct*100:.0f}% complete")

    # Auto-refresh while jobs are running
    time.sleep(3)
    st.rerun()

st.divider()

# =============================================================================
# Section B — Workspace Selection
# =============================================================================
st.subheader("🗂️ Workspaces")

try:
    workspaces = api.list_projects()
except Exception as e:
    st.error(f"Cannot load workspaces: {e}")
    st.stop()

if not workspaces:
    st.info("No workspaces yet — create one in the sidebar.")
    st.stop()

# Summaries fetched per workspace (small N, single query each)
cols = st.columns(3)
for i, ws in enumerate(workspaces):
    try:
        summary = api.workspace_summary(ws["id"])
    except Exception:
        summary = {}

    status_key, status_label = _workspace_status(summary)

    with cols[i % 3]:
        with st.container(border=True):
            st.write(f"**{ws['name']}**")
            st.caption(status_label)

            c1, c2 = st.columns(2)
            c1.metric("Videos", summary.get("total_videos", 0))
            c2.metric("Analyzed", summary.get("analyzed_videos", 0))

            dur = _fmt_duration(summary.get("total_duration_s"))
            sz = _fmt_size(summary.get("total_size_bytes"))
            info_parts = [p for p in [dur, sz] if p != "—"]
            if info_parts:
                st.caption(" · ".join(info_parts))

            if summary.get("queued_or_analyzing", 0) > 0:
                active = summary["queued_or_analyzing"]
                st.progress(
                    summary.get("analyzed_videos", 0) / max(summary.get("total_videos", 1), 1),
                    text=f"{active} analyzing",
                )

            if st.button("Open", key=f"open_ws_{ws['id']}", use_container_width=True):
                st.session_state["workspace"] = ws
                st.rerun()

