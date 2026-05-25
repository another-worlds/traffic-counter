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


def _health_badge(percentage: float) -> tuple[str, str]:
    if percentage >= 0.85:
        return "🟢", "#16a34a"
    if percentage >= 0.5:
        return "🟠", "#ea580c"
    return "🔴", "#dc2626"


def _fmt_eta(seconds: float) -> str:
    """Format seconds as a human-readable ETA string."""
    if seconds < 60:
        return f"~{int(seconds)}s"
    if seconds < 3600:
        return f"~{int(seconds / 60)}m"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"~{h}h {m:02d}m"


def _fmt_speed(ratio: float) -> str:
    """Format speed ratio as 'Nx faster than real time'."""
    if ratio >= 1:
        return f"{ratio:.1f}× speed"
    return f"1/{1/ratio:.1f}× speed"


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

# Build lookup dict for queue items by video_id (includes enriched segment data).
queue_by_id = {item["video_id"]: item for item in queue}

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

# Folder clustering (used both by dashboard and list)
folders = defaultdict(list)
for v in videos:
    folders[_folder_of(v)].append(v)

folder_rows = []
for folder, items in folders.items():
    count = len(items)
    analyzed_n = sum(1 for v in items if v["status"] == "analyzed")
    queued_n = sum(1 for v in items if v["status"] in ("queued", "analyzing"))
    err_n = sum(1 for v in items if v["status"] == "error")
    total_hours = sum(_duration_s(v) for v in items) / 3600.0
    processed_hours = sum(_duration_s(v) for v in items if v["status"] == "analyzed") / 3600.0
    completion = analyzed_n / max(1, count)
    # ETA for this folder: sum of ETAs from queue items that belong to this folder.
    folder_eta_s = sum(
        queue_by_id[v["id"]].get("eta_seconds") or 0.0
        for v in items
        if v["id"] in queue_by_id and queue_by_id[v["id"]].get("eta_seconds")
    )
    folder_rows.append({
        "folder": folder,
        "count": count,
        "analyzed": analyzed_n,
        "queued": queued_n,
        "errors": err_n,
        "total_hours": total_hours,
        "processed_hours": processed_hours,
        "completion": completion,
        "eta_s": folder_eta_s,
    })

folder_rows.sort(key=lambda r: (r["completion"], -r["count"]))

queue_status = defaultdict(int)
for item in queue:
    queue_status[item.get("status", "unknown")] += 1

queued_now = queue_status["queued"]
running_now = queue_status["analyzing"]
queue_health_ratio = analyzed / max(1, total)

# Queue controls
st.divider()
left, right = st.columns([3, 2])
with left:
    st.markdown("### Queue Controls")
    q1, q2, q3, q4 = st.columns(4)
    q1.metric("Ready to queue", unstarted)
    q2.metric("Queued now", queued_now)
    q3.metric("Running now", running_now)
    q4.metric("Queue health", f"{queue_health_ratio*100:.0f}%")

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        if st.button("▶ Queue all pending", disabled=unstarted == 0, use_container_width=True):
            try:
                result = api.analyze_pending_local_folder()
                st.success(f"Queued {result['queued']} video(s).")
                st.rerun()
            except api.APIError as exc:
                st.error(str(exc))
    with c2:
        if st.button("🧹 Clear stuck jobs", use_container_width=True):
            try:
                resp = api.reap_stale_jobs()
                count = resp.get("count", 0)
                if count:
                    st.success(
                        f"Cleared {count} stuck job(s). "
                        "Completed segments are preserved — click Analyze to resume from last checkpoint."
                    )
                else:
                    st.info("No stuck jobs.")
                st.rerun()
            except api.APIError as exc:
                st.error(str(exc))
    with c3:
        st.button("⟳ Refresh now", use_container_width=True, on_click=lambda: None)
    with c4:
        auto_refresh = st.checkbox("Auto-refresh (3s)", value=in_queue > 0)
with right:
    st.markdown("### Live Queue")
    st.caption(f"{len(queue)} item(s) currently queued/running across all workspaces")
    max_rows = st.slider("Visible queue rows", min_value=3, max_value=12, value=6, step=1)
    only_running = st.toggle("Show analyzing only", value=False)
    queue_items = [q for q in queue if (q.get("status") == "analyzing" if only_running else True)]
    for item in queue_items[:max_rows]:
        pct = float(item.get("progress_pct") or 0.0)
        with st.container(border=True):
            st.write(f"**{item['filename']}**")
            st.caption(f"{item['project_name']} · {item['status']}")
            if item["status"] == "analyzing":
                cs = item.get("completed_segments")
                ts = item.get("total_segments")
                if cs is not None and ts:
                    st.caption("🟩" * cs + "⬜" * (ts - cs))
                status_txt = item.get("worker_status_text") or f"{pct*100:.0f}%"
                st.progress(pct, text=status_txt)
                meta_parts = []
                if item.get("speed_ratio"):
                    meta_parts.append(_fmt_speed(item["speed_ratio"]))
                if item.get("eta_seconds"):
                    meta_parts.append(f"ETA {_fmt_eta(item['eta_seconds'])}")
                if meta_parts:
                    st.caption(" · ".join(meta_parts))

# Folder selection area
st.divider()
st.subheader("Folder-centric Tracking")

options = [f"{r['folder']} ({r['analyzed']}/{r['count']})" for r in folder_rows]
selected = st.selectbox("Selected folder", options=options)
sel_folder = selected.rsplit(" (", 1)[0]

selected_row = next(r for r in folder_rows if r["folder"] == sel_folder)
status_icon, accent = _health_badge(selected_row["completion"])

# Current folder dashboard placed above folder list
st.markdown("### Current Folder Dashboard")
st.markdown(
    f"<div style='padding:0.55rem 0.8rem;border-left:6px solid {accent};background:#f8fafc;border-radius:8px;'>"
    f"<strong>{status_icon} {selected_row['folder']}</strong>"
    f"<br/><span style='color:#475569;'>Videos: {selected_row['count']} · "
    f"Analyzed: {selected_row['analyzed']} · Queue: {selected_row['queued']} · Errors: {selected_row['errors']}</span>"
    "</div>",
    unsafe_allow_html=True,
)

fc1, fc2, fc3, fc4 = st.columns(4)
fc1.metric("Total hours in folder", f"{selected_row['total_hours']:.2f}h")
fc2.metric("Processed hours", f"{selected_row['processed_hours']:.2f}h")
fc3.metric("Folder completion", f"{selected_row['completion']*100:.1f}%")
if selected_row["eta_s"] > 0:
    fc4.metric("Folder ETA", _fmt_eta(selected_row["eta_s"]))
else:
    fc4.metric("Folder ETA", "—")
st.progress(selected_row["completion"], text=f"{status_icon} Folder completion {selected_row['completion']*100:.1f}%")

st.markdown("### Folder Video List")
folder_videos = sorted(folders[sel_folder], key=lambda v: (v["status"], v["filename"]))
status_chip = {
    "uploaded": "⬜ Uploaded",
    "queued": "🟨 Queued",
    "analyzing": "🟧 Analyzing",
    "analyzed": "🟩 Analyzed",
    "error": "🟥 Error",
}

for v in folder_videos:
    q_item = queue_by_id.get(v["id"])
    with st.container(border=True):
        c1, c2, c3 = st.columns([5, 2, 2])
        with c1:
            st.markdown(f"**🎞️ {v['filename']}**")
            if v.get("local_source_path"):
                st.caption(v["local_source_path"])
        with c2:
            st.markdown(status_chip.get(v["status"], v["status"]))
            duration = _duration_s(v) / 60.0
            total_segs = v.get("total_segments")
            if total_segs:
                st.caption(f"{duration:.1f} min · {total_segs} segments")
            else:
                st.caption(f"{duration:.1f} min")
            if v["status"] == "analyzing":
                pct = v.get("progress_pct") or 0.0
                status_txt = (q_item or {}).get("worker_status_text") or f"{pct*100:.0f}%"
                st.progress(pct, text=status_txt)
                if q_item:
                    speed = q_item.get("speed_ratio")
                    eta = q_item.get("eta_seconds")
                    caps = []
                    if speed:
                        caps.append(_fmt_speed(speed))
                    if eta:
                        caps.append(f"ETA {_fmt_eta(eta)}")
                    if caps:
                        st.caption(" · ".join(caps))
            elif v["status"] == "error":
                err = v.get("error_message") or ""
                # Show a concise first line; full message in expander.
                first_line = err.splitlines()[0] if err else "Unknown error"
                st.caption(f"⚠️ {first_line[:80]}")
        with c3:
            if v["status"] in {"uploaded", "error"}:
                btn_label = "Analyze" if v["status"] == "uploaded" else "Retry"
                if v["status"] == "error" and v.get("total_segments"):
                    btn_label = "Resume"
                if st.button(btn_label, key=f"analyze_{v['id']}", use_container_width=True):
                    try:
                        api.analyze_video(v["id"])
                        st.rerun()
                    except api.APIError as exc:
                        st.error(str(exc))
            else:
                st.caption("Managed by queue")

        # Expandable segment breakdown (shown when segments exist)
        seg_count = v.get("total_segments") or 0
        if seg_count > 0:
            completed_segs = (q_item or {}).get("completed_segments") or 0
            label = f"Segments ({completed_segs}/{seg_count} done)"
            with st.expander(label, expanded=False):
                try:
                    segs = api.get_video_segments(v["id"])
                except api.APIError:
                    st.warning("Could not load segment data.")
                    segs = []
                if segs:
                    seg_status_icon = {
                        "pending": "⬜",
                        "analyzing": "🟧",
                        "done": "🟩",
                        "error": "🟥",
                    }
                    for s in segs:
                        t0 = s["start_time_s"]
                        t1 = s["end_time_s"]
                        h0 = int(t0 // 3600)
                        h1 = int(t1 // 3600)
                        time_range = f"{h0}:00–{h1}:00"
                        icon = seg_status_icon.get(s["status"], "❓")
                        parts = [f"{icon} Seg {s['segment_idx']+1}: {time_range}"]
                        if s["status"] == "done":
                            if s.get("num_tracks") is not None:
                                parts.append(f"{s['num_tracks']} tracks")
                            if s.get("wall_clock_s"):
                                wc = s["wall_clock_s"]
                                vid_s = t1 - t0
                                ratio = vid_s / wc if wc > 0 else None
                                parts.append(f"{wc/60:.1f} min wall")
                                if ratio:
                                    parts.append(_fmt_speed(ratio))
                        elif s["status"] == "error" and s.get("error_message"):
                            first = s["error_message"].splitlines()[0][:60]
                            parts.append(f"⚠️ {first}")
                        elif s["status"] == "analyzing":
                            parts.append("⏳ In progress")
                        st.caption(" · ".join(parts))
                elif v.get("status") == "analyzed":
                    st.caption("Segment data not available (legacy single-parquet video).")
                else:
                    st.caption("No segments planned yet.")

        # Full error details expander
        if v["status"] == "error" and v.get("error_message"):
            with st.expander("Full error details", expanded=False):
                st.code(v["error_message"], language=None)

if auto_refresh and in_queue > 0:
    time.sleep(3)
    st.rerun()
