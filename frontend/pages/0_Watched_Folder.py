import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

import api_client as api

st.set_page_config(page_title="Watched Folder", page_icon="📂", layout="wide")
st.title("📂 Watched Folder")

WATCH_PATH = os.environ.get("WATCHER_WATCH_PATH", "/mnt/yandex-videos")
st.caption(
    f"Watched path: `{WATCH_PATH}` · "
    "This is now the primary ingestion pipeline. Place files in nested folders and they will be tracked automatically."
)


def _duration_s(video: dict) -> float:
    return float(video.get("duration_s") or 0.0)


def _processing_time_s(video: dict, now_utc: datetime) -> float:
    start = video.get("started_analyzing_at")
    if not start:
        return 0.0
    try:
        start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
    except ValueError:
        return 0.0

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    end = video.get("analyzed_at")
    if end:
        try:
            end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        except ValueError:
            end_dt = now_utc
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
    elif video.get("status") == "analyzing":
        end_dt = now_utc
    else:
        return 0.0

    return max(0.0, (end_dt - start_dt).total_seconds())


def _folder_of(video: dict) -> str:
    local_path = video.get("local_source_path") or ""
    if local_path:
        p = Path(local_path)
        return str(p.parent) if str(p.parent) else "(root)"
    return "(unknown)"


try:
    videos = api.list_local_folder_videos()
    queue = api.worker_status()
except api.APIError as exc:
    st.error(f"Could not reach API: {exc}")
    st.stop()

if not videos:
    st.info(
        "No videos indexed yet. The watcher service will register them automatically once files appear in "
        f"`{WATCH_PATH}`."
    )
    st.stop()

# Dashboard stats
status_counts = defaultdict(int)
for v in videos:
    status_counts[v["status"]] += 1

total = len(videos)
analyzed = status_counts["analyzed"]
in_queue = status_counts["queued"] + status_counts["analyzing"]
errors = status_counts["error"]
unstarted = status_counts["uploaded"]

all_duration_s = sum(_duration_s(v) for v in videos)
analyzed_duration_s = sum(_duration_s(v) for v in videos if v["status"] == "analyzed")
now_utc = datetime.now(timezone.utc)
total_processing_time_s = sum(_processing_time_s(v, now_utc) for v in videos)

running = [v for v in videos if v["status"] == "analyzing" and v.get("progress_pct") is not None]
queue_avg_progress = (sum(v["progress_pct"] for v in running) / len(running)) if running else 0.0

st.subheader("Processing Dashboard")
mc1, mc2, mc3, mc4 = st.columns(4)
mc1.metric("Total videos", total)
mc2.metric("Completed", analyzed, f"{(analyzed/total)*100:.1f}%")
mc3.metric("Queue + running", in_queue)
mc4.metric("Errors", errors)

mc5, mc6, mc7, mc8 = st.columns(4)
mc5.metric("Yandex folder hours", f"{all_duration_s/3600:.1f}h")
mc6.metric("Processed hours", f"{analyzed_duration_s/3600:.1f}h")
mc7.metric("Avg running progress", f"{queue_avg_progress*100:.0f}%")
mc8.metric("Processing time spent", f"{total_processing_time_s/3600:.1f}h")

st.progress(analyzed / max(1, total), text=f"Overall completion: {analyzed}/{total} videos")

# Queue controls
st.divider()
left, right = st.columns([3, 2])
with left:
    st.markdown("### Queue Controls")
    c1, c2, c3 = st.columns(3)
    with c1:
        if st.button("▶ Queue all pending", disabled=unstarted == 0, use_container_width=True):
            try:
                result = api.analyze_pending_local_folder()
                st.success(f"Queued {result['queued']} video(s).")
                st.rerun()
            except api.APIError as exc:
                st.error(str(exc))
    with c2:
        st.button("⟳ Refresh now", use_container_width=True, on_click=lambda: None)
    with c3:
        auto_refresh = st.checkbox("Auto-refresh (3s)", value=in_queue > 0)
with right:
    st.markdown("### Live Queue")
    st.caption(f"{len(queue)} item(s) currently queued/running across all workspaces")
    for item in queue[:6]:
        pct = float(item.get("progress_pct") or 0.0)
        st.write(f"**{item['filename']}** · {item['project_name']} · {item['status']}")
        if item["status"] == "analyzing":
            st.progress(pct)

# Folder-clustered visualization
folders = defaultdict(list)
for v in videos:
    folders[_folder_of(v)].append(v)

folder_rows = []
for folder, items in folders.items():
    count = len(items)
    analyzed_n = sum(1 for v in items if v["status"] == "analyzed")
    queued_n = sum(1 for v in items if v["status"] in ("queued", "analyzing"))
    err_n = sum(1 for v in items if v["status"] == "error")
    duration_min = sum(_duration_s(v) for v in items) / 60.0
    folder_rows.append({
        "folder": folder,
        "count": count,
        "analyzed": analyzed_n,
        "queued": queued_n,
        "errors": err_n,
        "minutes": duration_min,
        "completion": analyzed_n / max(1, count),
    })

folder_rows.sort(key=lambda r: (r["completion"], -r["count"]))

st.divider()
st.subheader("Folder-centric Tracking")
left, right = st.columns([1, 2])

with left:
    options = [f"{r['folder']} ({r['analyzed']}/{r['count']})" for r in folder_rows]
    selected = st.radio("Folders", options=options, label_visibility="collapsed")
    sel_folder = selected.rsplit(" (", 1)[0]
    st.markdown("#### Folder statistics")
    row = next(r for r in folder_rows if r["folder"] == sel_folder)
    st.write(f"**{row['folder']}**")
    st.caption(f"Total videos: {row['count']}")
    st.caption(f"Analyzed: {row['analyzed']} · In queue: {row['queued']} · Errors: {row['errors']}")
    st.caption(f"Total duration: {row['minutes']:.1f} minutes")
    st.progress(row["completion"], text=f"Completion {row['completion']*100:.0f}%")

with right:
    st.markdown("#### Videos in selected folder")
    folder_videos = sorted(folders[sel_folder], key=lambda v: (v["status"], v["filename"]))
    for v in folder_videos:
        with st.container(border=True):
            c1, c2, c3 = st.columns([4, 2, 2])
            with c1:
                st.write(f"**{v['filename']}**")
                if v.get("local_source_path"):
                    st.caption(v["local_source_path"])
            with c2:
                st.write(v["status"])
                if v["status"] == "analyzing" and v.get("progress_pct") is not None:
                    st.progress(v["progress_pct"])
            with c3:
                if v["status"] in {"uploaded", "error"}:
                    if st.button("Analyze", key=f"analyze_{v['id']}"):
                        try:
                            api.analyze_video(v["id"])
                            st.rerun()
                        except api.APIError as exc:
                            st.error(str(exc))

if auto_refresh and in_queue > 0:
    time.sleep(3)
    st.rerun()
