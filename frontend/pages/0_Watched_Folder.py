"""Yandex Disk auto-import: dashboard for the watched-folder processing queue."""
import time

import pandas as pd
import streamlit as st

import api_client as api

st.set_page_config(page_title="Watched Folder", page_icon="📂", layout="wide")
st.title("📂 Watched Folder")

# ── fetch dashboard (single API call) ────────────────────────────────────────
try:
    d = api.get_local_folder_dashboard()
except api.APIError as exc:
    st.error(f"Could not reach API: {exc}")
    st.stop()

counts    = d["counts"]
paused    = d["paused"]
analyzing = d.get("currently_analyzing", [])
errors    = d.get("recent_errors", [])


def _fmt_eta(sec):
    if sec is None:
        return "—"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s   = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


# ── summary metrics ───────────────────────────────────────────────────────────
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Total indexed",      counts["total"])
c2.metric("Analyzed",           counts["analyzed"])
c3.metric("In queue / running", counts["queued"] + counts["analyzing"])
c4.metric("Errors",             counts["error"])
c5.metric("Throughput",         f'{d["throughput_per_hour"]:.0f}/hr')
c6.metric("Queue ETA",          _fmt_eta(d.get("queue_eta_seconds")))

st.divider()

# ── control row ───────────────────────────────────────────────────────────────
btn_col1, btn_col2, btn_col3, refresh_col = st.columns([2, 2, 2, 3])

with btn_col1:
    if st.button("▶ Analyze all unprocessed",
                 disabled=counts["uploaded"] == 0,
                 help="Queue every video that has not been analyzed yet."):
        try:
            result = api.analyze_pending_local_folder()
            st.success(f"Queued {result['queued']} video(s).")
            st.rerun()
        except api.APIError as exc:
            st.error(str(exc))

with btn_col2:
    if paused:
        if st.button("▶ Resume processing", type="primary"):
            try:
                api.resume_worker()
                st.rerun()
            except api.APIError as exc:
                st.error(str(exc))
    else:
        if st.button("⏸ Pause processing",
                     help="Current video finishes; no new claims until resumed."):
            try:
                api.pause_worker()
                st.rerun()
            except api.APIError as exc:
                st.error(str(exc))

with btn_col3:
    if st.button("🔄 Retry all errors",
                 disabled=counts["error"] == 0,
                 help="Re-queue all videos currently in error state."):
        try:
            result = api.retry_local_folder_errors()
            st.success(f"Re-queued {result['queued']} video(s).")
            st.rerun()
        except api.APIError as exc:
            st.error(str(exc))

with refresh_col:
    in_flight = counts["queued"] + counts["analyzing"]
    auto_refresh = st.checkbox(
        "Auto-refresh",
        value=in_flight > 0,
        help="3 s while analyzing; 10 s while only queued.",
    )

if paused:
    st.warning("⏸ Processing is paused. Click **▶ Resume processing** to continue.")

# ── currently analyzing ───────────────────────────────────────────────────────
if analyzing:
    st.subheader("Currently analyzing")
    for v in analyzing:
        with st.container(border=True):
            pct = v["progress_pct"] or 0.0
            label = f"**{v['filename']}** — {pct*100:.0f}%"
            if v.get("eta_s") is not None:
                label += f"  ·  ETA {_fmt_eta(v['eta_s'])}"
            st.markdown(label)
            st.progress(pct)

# ── recent errors expander ────────────────────────────────────────────────────
if errors:
    with st.expander(f"⚠️ Recent errors ({counts['error']} total)", expanded=False):
        for e in errors:
            retry_badge = f"  ·  retry {e['retries']}" if e.get("retries") else ""
            st.markdown(f"**{e['filename']}**{retry_badge}")
            if e.get("error_message"):
                st.caption(e["error_message"])
            col_r, _ = st.columns([1, 5])
            with col_r:
                if st.button("Re-queue", key=f"retry_{e['id']}"):
                    try:
                        api.analyze_video(e["id"])
                        st.rerun()
                    except api.APIError as exc:
                        st.error(str(exc))

# ── indexed-videos table ──────────────────────────────────────────────────────
if counts["total"] == 0:
    st.info(
        "No videos indexed yet. "
        "The watcher service registers them automatically once files appear in the watched folder."
    )
    st.stop()

st.subheader(f"Indexed videos ({counts['total']})")

STATUS_EMOJI = {
    "uploaded":  "🔵",
    "queued":    "🟡",
    "analyzing": "🟠",
    "analyzed":  "🟢",
    "error":     "🔴",
}

try:
    videos = api.list_local_folder_videos()
except api.APIError as exc:
    st.error(f"Could not load video list: {exc}")
    st.stop()

if videos:
    rows = []
    for v in videos:
        emoji = STATUS_EMOJI.get(v["status"], "⚪")
        size = v.get("size_bytes")
        size_str = (
            f"{size/1e9:.2f} GB" if size and size > 1e9
            else f"{size/1e6:.1f} MB" if size
            else ""
        )
        dur = v.get("duration_s")
        dur_str = f"{dur:.0f}s" if dur else ""
        err_excerpt = ""
        if v["status"] == "error" and v.get("error_message"):
            err_excerpt = v["error_message"][:80]
        rows.append({
            "": emoji,
            "Filename": v["filename"],
            "Status": v["status"],
            "Size": size_str,
            "Duration": dur_str,
            "Retries": v.get("retries") or 0,
            "Error": err_excerpt,
        })

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        column_config={
            "":         st.column_config.TextColumn(width="small"),
            "Filename": st.column_config.TextColumn(width="large"),
            "Status":   st.column_config.TextColumn(width="small"),
            "Size":     st.column_config.TextColumn(width="small"),
            "Duration": st.column_config.TextColumn(width="small"),
            "Retries":  st.column_config.NumberColumn(width="small"),
            "Error":    st.column_config.TextColumn(width="medium"),
        },
    )

# ── adaptive auto-refresh ─────────────────────────────────────────────────────
if auto_refresh and in_flight > 0:
    time.sleep(3 if analyzing else 10)
    st.rerun()
