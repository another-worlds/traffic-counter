import io
import os
import re
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

import httpx

import api_client as api
import timestamp_client as ts_api

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


def _safe_zip_part(text: str) -> str:
    cleaned = re.sub(r"[^\w.\-]+", "_", str(text).strip())
    return cleaned.strip("._") or "item"


def _xlsx_folder_path(video: dict) -> str:
    local_path = video.get("local_source_path") or ""
    if local_path:
        parent = Path(local_path).parent
        try:
            rel = parent.relative_to(Path(WATCH_PATH))
            parts = [_safe_zip_part(part) for part in rel.parts if part not in ("", ".")]
            if parts:
                return "/".join(parts)
        except ValueError:
            pass
        return _safe_zip_part(parent.name or "root")
    return _safe_zip_part(Path(_folder_of(video)).name or "root")


def _xlsx_archive_name(video: dict, export_filename: str) -> str:
    folder = _xlsx_folder_path(video)
    base = _safe_zip_part(Path(export_filename).name or video.get("filename", "counts.xlsx"))
    if not base.lower().endswith(".xlsx"):
        base = f"{base}.xlsx"
    return f"{folder}/{base}"


@st.cache_data(ttl=30, show_spinner=False)
def _cached_video_lines(video_id: str) -> list:
    return api.list_lines(video_id)


def _bulk_export_xlsx_zip(
    videos: list,
    *,
    apply_timestamp_correction: bool,
    progress_cb=None,
) -> tuple[bytes, dict]:
    """Build one XLSX per analyzed video (with lines) and return a ZIP archive."""
    analyzed = [v for v in videos if v.get("status") == "analyzed"]
    summary = {
        "requested": len(analyzed),
        "exported": 0,
        "skipped_no_lines": [],
        "failed": [],
        "files": [],
    }
    if not analyzed:
        return b"", summary

    buffer = io.BytesIO()
    used_names: set[str] = set()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, video in enumerate(analyzed, start=1):
            if progress_cb:
                progress_cb(idx, len(analyzed), video)

            try:
                lines = _cached_video_lines(str(video["id"]))
            except api.APIError as exc:
                summary["failed"].append({
                    "filename": video.get("filename"),
                    "error": str(exc),
                })
                continue

            if not lines:
                summary["skipped_no_lines"].append(video.get("filename") or video.get("id"))
                continue

            line_ids = [str(ln["id"]) for ln in lines]
            try:
                job = api.start_export(
                    str(video["id"]),
                    line_ids,
                    apply_timestamp_correction=apply_timestamp_correction,
                )
                status = api.wait_for_export(str(job["job_id"]))
                data = api.download_export(str(job["job_id"]))
                arcname = _xlsx_archive_name(video, str(status.get("filename") or "counts.xlsx"))
                if arcname in used_names:
                    stem, suffix = arcname.rsplit(".", 1)
                    arcname = f"{stem}_{_safe_zip_part(video['id'][:8])}.{suffix}"
                used_names.add(arcname)
                zf.writestr(arcname, data)
                summary["exported"] += 1
                summary["files"].append(arcname)
            except (api.APIError, TimeoutError) as exc:
                summary["failed"].append({
                    "filename": video.get("filename"),
                    "error": str(exc),
                })

    buffer.seek(0)
    return buffer.getvalue(), summary


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

# --- Throughput & batch ETA ---
# avg_speed: video-seconds per wall-clock-second, per worker.
# Primary source: fully-analyzed videos with both timestamps.
analyzed_timed = [
    v for v in videos
    if v["status"] == "analyzed"
    and _duration_s(v) > 0
    and _processing_time_s(v, now_utc) > 0
]
if analyzed_timed:
    speeds = [_duration_s(v) / _processing_time_s(v, now_utc) for v in analyzed_timed]
    avg_speed = sum(speeds) / len(speeds)
else:
    # Fallback: use speed_ratio from currently-analyzing queue items.
    live_speeds = [q["speed_ratio"] for q in queue if q.get("speed_ratio")]
    avg_speed = sum(live_speeds) / len(live_speeds) if live_speeds else 0.0

# Remaining video content (uploaded + queued = full duration; analyzing = partial).
remaining_video_s = sum(_duration_s(v) for v in videos if v["status"] in ("uploaded", "queued"))
remaining_video_s += sum(
    _duration_s(v) * (1.0 - float(v.get("progress_pct") or 0.0))
    for v in videos if v["status"] == "analyzing"
)

# Batch ETA: divide remaining content by (speed × simultaneous workers).
# _processing_time_s sums per-video elapsed time so avg_speed is per-worker;
# multiplying by n_concurrent converts to effective batch throughput.
n_concurrent = max(1, len([v for v in videos if v["status"] == "analyzing"]))
batch_eta_s = (
    remaining_video_s / (avg_speed * n_concurrent)
    if avg_speed > 0 and remaining_video_s > 0
    else None
)

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

mc9, mc10, mc11, mc12 = st.columns(4)
mc9.metric(
    "Throughput",
    f"{avg_speed:.1f}× speed" if avg_speed else "—",
    "video-hr / worker-hr" if avg_speed else "need ≥1 completed video",
)
mc10.metric(
    "Batch ETA",
    _fmt_eta(batch_eta_s) if batch_eta_s else "—",
    f"{n_concurrent} worker{'s' if n_concurrent != 1 else ''}" if batch_eta_s else None,
)
mc11.metric("Remaining content", f"{remaining_video_s / 3600:.1f}h")
mc12.metric("Videos remaining", unstarted + in_queue)

st.progress(analyzed / max(1, total), text=f"Overall completion: {analyzed}/{total} videos")


@st.cache_data(ttl=30, show_spinner=False)
def _cached_video_segments(video_id: str) -> list:
    return api.get_video_segments(video_id)


@st.cache_data(ttl=5, show_spinner=False)
def _cached_timestamp_statuses(video_keys: tuple) -> dict:
    """video_keys: tuple of (id, project_id) pairs for cache invalidation."""
    videos = [{"id": vid, "project_id": pid, "status": "analyzed"} for vid, pid in video_keys]
    return ts_api.fetch_statuses_for_videos(videos)


@st.cache_data(ttl=15, show_spinner=False)
def _cached_region_preview(video_id: str, project_id: str, cache_bust: str) -> bytes | None:
    return ts_api.get_region_preview_bytes(video_id, project_id)


def _region_preview_cache_bust(doc: dict) -> str:
    progress = doc.get("progress") or {}
    return str(
        doc.get("completed_at")
        or f"{progress.get('phase', '')}-{progress.get('percent', '')}"
    )


def _region_preview_caption(doc: dict) -> str:
    region = doc.get("region") or {}
    method = str(region.get("method", ""))
    if method.startswith("manual"):
        label = "Manual region"
    elif "ocr_timestamp" in method:
        label = "Auto-detected timestamp"
    elif method:
        label = f"Auto-detected ({method})"
    else:
        label = "Timestamp region"
    if region.get("w") and region.get("h"):
        label += f" · {region['w']}×{region['h']}px"
    return label


_DELETABLE_TS_STATUSES = frozenset({"done", "error", "cancelled"})


def _clear_ts_dashboard_caches() -> None:
    _cached_timestamp_statuses.clear()
    _cached_region_preview.clear()


def _delete_timestamp_analysis(v: dict, *, key: str) -> None:
    try:
        ts_api.delete_timestamp_scan(str(v["id"]), str(v["project_id"]))
        _clear_ts_dashboard_caches()
        st.toast(f"Deleted timestamp analysis for {v['filename']}")
        st.rerun()
    except ts_api.TimestampAPIError as exc:
        st.error(str(exc))


def _delete_all_timestamp_analyses(videos: list) -> None:
    if not videos:
        return
    with st.spinner(f"Deleting timestamp analysis for {len(videos)} video(s)…"):
        result = ts_api.delete_timestamp_scans_bulk(videos)
    _clear_ts_dashboard_caches()
    deleted = int(result.get("deleted") or 0)
    failed = result.get("failed") or []
    if deleted:
        st.success(f"Deleted timestamp analysis for {deleted} video(s). Auto-scan will queue again.")
    if failed:
        st.warning(f"Could not delete {len(failed)} video(s):")
        for item in failed[:5]:
            st.caption(f"{item.get('filename', item.get('id'))}: {item.get('error', 'unknown error')}")
        if len(failed) > 5:
            st.caption(f"…and {len(failed) - 5} more")
    if deleted or failed:
        st.session_state.pop("ts_delete_all_confirm", None)
        st.rerun()


def _render_ts_region_preview(v: dict, doc: dict) -> None:
    artifacts = doc.get("artifacts") or {}
    if not artifacts.get("region_preview"):
        return
    preview = _cached_region_preview(
        str(v["id"]),
        str(v["project_id"]),
        _region_preview_cache_bust(doc),
    )
    if preview:
        st.image(preview, caption=_region_preview_caption(doc), width=300)


def _render_timestamp_dashboard(videos: list, analyzed_count: int) -> int:
    """Render OSD timestamp scan dashboard. Returns count of in-progress scans."""
    st.divider()
    st.subheader("Timestamp Scan Dashboard")
    st.caption(
        "Auto-queued for every analyzed video. Locates burned-in timestamps, "
        "detects footage gaps, and prepares gap-corrected counts."
    )

    if analyzed_count == 0:
        st.info("No analyzed videos yet — timestamp scans start after vehicle tracking completes.")
        return 0

    if not ts_api.health_check():
        st.warning("Timestamp API unreachable (port 8200). Start `timestamp-correction-api` and worker.")
        return 0

    analyzed_videos = [v for v in videos if v["status"] == "analyzed"]
    keys = tuple((str(v["id"]), str(v["project_id"])) for v in analyzed_videos)
    ts_by_id = _cached_timestamp_statuses(keys)

    ts_counts = defaultdict(int)
    for doc in ts_by_id.values():
        ts_counts[doc.get("status", "pending")] += 1

    ts_done = ts_counts["done"]
    ts_processing = ts_counts["processing"]
    ts_pending = ts_counts["pending"]
    ts_errors = ts_counts["error"] + ts_counts["unreachable"]
    ts_total = len(analyzed_videos)

    tc1, tc2, tc3, tc4, tc5 = st.columns(5)
    tc1.metric("Scans complete", ts_done, f"{(ts_done / max(1, ts_total)) * 100:.0f}%")
    tc2.metric("Scanning now", ts_processing)
    tc3.metric("Queued", ts_pending)
    tc4.metric("Scan errors", ts_errors)
    tc5.metric("Analyzed videos", ts_total)

    st.progress(
        ts_done / max(1, ts_total),
        text=f"Timestamp scans: {ts_done}/{ts_total} complete",
    )

    clearable_all = [
        v for v in analyzed_videos
        if ts_by_id.get(v["id"], {}).get("status") in _DELETABLE_TS_STATUSES
    ]
    if clearable_all or ts_processing:
        with st.container(border=True):
            st.markdown("**Bulk actions**")
            if clearable_all:
                st.caption(
                    f"Clear gap maps, timelines, and region artifacts for "
                    f"{len(clearable_all)} video(s). Each resets to pending and auto-scan will queue again."
                )
            if ts_processing:
                st.caption(
                    f"{ts_processing} scan(s) still running — stop those first; they cannot be bulk-deleted."
                )
            confirm_col, action_col = st.columns([3, 1])
            with confirm_col:
                st.checkbox(
                    "I understand this permanently removes all saved timestamp corrections",
                    key="ts_delete_all_confirm",
                    disabled=not clearable_all,
                )
            with action_col:
                if st.button(
                    f"Delete all ({len(clearable_all)})",
                    key="ts_delete_all_btn",
                    type="secondary",
                    disabled=not clearable_all or not st.session_state.get("ts_delete_all_confirm"),
                    use_container_width=True,
                ):
                    _delete_all_timestamp_analyses(clearable_all)

    # Aggregate gap stats from completed scans
    total_gaps = 0
    total_gap_fraction = 0.0
    done_with_stats = 0
    for doc in ts_by_id.values():
        if doc.get("status") != "done":
            continue
        stats = doc.get("stats") or {}
        if stats:
            done_with_stats += 1
            total_gaps += int(stats.get("num_gaps") or 0)
            total_gap_fraction += float(stats.get("gap_fraction") or 0.0)
    if done_with_stats:
        gc1, gc2, gc3 = st.columns(3)
        gc1.metric("Avg gaps per video", f"{total_gaps / done_with_stats:.1f}")
        gc2.metric("Avg footage in gaps", f"{(total_gap_fraction / done_with_stats) * 100:.1f}%")
        gc3.metric("Videos with gap map", done_with_stats)

    # Live processing queue
    processing_items = []
    for v in analyzed_videos:
        doc = ts_by_id.get(v["id"], {})
        if doc.get("status") != "processing":
            continue
        progress = doc.get("progress") or {}
        processing_items.append({
            "video": v,
            "progress": progress,
            "message": progress.get("message") or progress.get("phase_label") or "Scanning…",
            "percent": float(progress.get("percent") or 0.0),
            "phase": progress.get("phase_label") or progress.get("phase") or "processing",
        })

    if processing_items:
        st.markdown("### Live Timestamp Scans")
        for item in processing_items[:8]:
            v = item["video"]
            doc = ts_by_id.get(v["id"], {})
            with st.container(border=True):
                col_main, col_preview = st.columns([3, 1])
                with col_main:
                    col_title, col_stop = st.columns([4, 1])
                    with col_title:
                        st.write(f"**{v['filename']}**")
                        st.caption(f"{_folder_of(v)} · {item['phase']}")
                    with col_stop:
                        if st.button("Stop", key=f"ts_stop_{v['id']}", type="secondary"):
                            try:
                                ts_api.stop_timestamp_scan(str(v["id"]), str(v["project_id"]))
                                st.toast(f"Stopped — auto-scan off for {v['filename']}")
                                st.rerun()
                            except ts_api.TimestampAPIError as exc:
                                st.error(str(exc))
                    st.progress(
                        min(1.0, item["percent"] / 100.0),
                        text=f"{item['percent']:.0f}% — {item['message']}",
                    )
                with col_preview:
                    _render_ts_region_preview(v, doc)
        if len(processing_items) > 8:
            st.caption(f"…and {len(processing_items) - 8} more scanning")

    done_items = [
        v for v in analyzed_videos
        if ts_by_id.get(v["id"], {}).get("status") == "done"
    ]
    if done_items:
        st.markdown("### Completed Timestamp Scans")
        for v in done_items[:12]:
            doc = ts_by_id.get(v["id"], {})
            stats = doc.get("stats") or {}
            with st.container(border=True):
                col_main, col_preview = st.columns([3, 1])
                with col_main:
                    col_title, col_delete = st.columns([4, 1])
                    with col_title:
                        st.write(f"**{v['filename']}**")
                        st.caption(f"{_folder_of(v)}")
                        gaps = stats.get("num_gaps")
                        gap_frac = stats.get("gap_fraction")
                        stride = stats.get("sample_stride_frames")
                        fps = stats.get("fps")
                        interval_min = stats.get("sample_interval_minutes")
                        if stride and fps:
                            interval_txt = (
                                f"{interval_min:g} min"
                                if interval_min is not None
                                else f"{stats.get('sample_interval_s', 60):.0f}s"
                            )
                            st.caption(f"OCR stride: every {stride} frames ({fps} fps · {interval_txt})")
                        parsed_frac = stats.get("parsed_fraction", stats.get("coherent_fraction", stats.get("trusted_fraction")))
                        hours_cov = stats.get("hours_with_coverage")
                        if parsed_frac is not None:
                            st.caption(
                                f"Parsed: {float(parsed_frac) * 100:.0f}% of 1-min bins"
                                + (f" · {hours_cov}h covered" if hours_cov is not None else "")
                            )
                        if gaps is not None:
                            gap_txt = f"{gaps} gap(s)"
                            if gap_frac is not None:
                                gap_txt += f" · {float(gap_frac) * 100:.1f}% footage in gaps"
                            st.caption(gap_txt)
                    with col_delete:
                        if st.button("Delete", key=f"ts_del_done_{v['id']}", type="secondary"):
                            _delete_timestamp_analysis(v, key=f"ts_del_done_{v['id']}")
                with col_preview:
                    _render_ts_region_preview(v, doc)
        if len(done_items) > 12:
            st.caption(f"…and {len(done_items) - 12} more completed scans")

    # Pending queue preview
    pending_items = [
        v for v in analyzed_videos
        if ts_by_id.get(v["id"], {}).get("status") in ("pending", None)
    ]
    if pending_items and not processing_items:
        st.markdown("### Timestamp Queue")
        st.caption(f"{len(pending_items)} analyzed video(s) waiting for auto-scan")
        for v in pending_items[:5]:
            col_cap, col_stop = st.columns([4, 1])
            with col_cap:
                st.caption(f"⏳ {v['filename']} · {_folder_of(v)}")
            with col_stop:
                if st.button("Stop", key=f"ts_stop_pending_{v['id']}", type="secondary"):
                    try:
                        ts_api.stop_timestamp_scan(str(v["id"]), str(v["project_id"]))
                        st.toast(f"Stopped — auto-scan off for {v['filename']}")
                        st.rerun()
                    except ts_api.TimestampAPIError as exc:
                        st.error(str(exc))
        if len(pending_items) > 5:
            st.caption(f"…and {len(pending_items) - 5} more")

    # Per-video status table (collapsible)
    with st.expander(f"All timestamp scan statuses ({ts_total})", expanded=False):
        status_icon = {
            "done": "🟩",
            "processing": "🟧",
            "pending": "🟨",
            "cancelled": "⏹",
            "error": "🟥",
            "unreachable": "⚫",
        }
        rows = []
        for v in sorted(analyzed_videos, key=lambda x: (ts_by_id.get(x["id"], {}).get("status", "z"), x["filename"])):
            doc = ts_by_id.get(v["id"], {"status": "pending"})
            st_key = doc.get("status", "pending")
            progress = doc.get("progress") or {}
            stats = doc.get("stats") or {}
            pct = progress.get("percent")
            rows.append({
                "status": f"{status_icon.get(st_key, '❓')} {st_key}",
                "file": v["filename"],
                "folder": _folder_of(v),
                "progress": f"{pct:.0f}%" if pct is not None else "—",
                "gaps": stats.get("num_gaps", "—"),
                "gap_%": (
                    f"{float(stats['gap_fraction']) * 100:.1f}%"
                    if stats.get("gap_fraction") is not None else "—"
                ),
                "phase": progress.get("phase_label") or "—",
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)

        clearable = [
            v for v in analyzed_videos
            if ts_by_id.get(v["id"], {}).get("status") in _DELETABLE_TS_STATUSES
        ]
        if clearable:
            st.markdown("**Delete timestamp analysis**")
            st.caption("Removes gap map, timeline, and region artifacts. Auto-scan will queue again.")
            for v in clearable[:20]:
                doc = ts_by_id.get(v["id"], {})
                st_key = doc.get("status", "pending")
                col_cap, col_del = st.columns([5, 1])
                with col_cap:
                    st.caption(f"{v['filename']} · {_folder_of(v)} · {st_key}")
                with col_del:
                    if st.button("Delete", key=f"ts_del_tbl_{v['id']}", type="secondary"):
                        _delete_timestamp_analysis(v, key=f"ts_del_tbl_{v['id']}")

        preview_videos = [
            v for v in analyzed_videos
            if ts_by_id.get(v["id"], {}).get("status") in ("processing", "done")
            and (ts_by_id.get(v["id"], {}).get("artifacts") or {}).get("region_preview")
        ]
        if preview_videos:
            st.markdown("**Region previews**")
            cols = st.columns(min(4, len(preview_videos[:8])))
            for i, v in enumerate(preview_videos[:8]):
                doc = ts_by_id.get(v["id"], {})
                with cols[i % len(cols)]:
                    st.caption(v["filename"])
                    _render_ts_region_preview(v, doc)

    return ts_processing


ts_in_progress = _render_timestamp_dashboard(videos, analyzed)

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
        auto_refresh = st.checkbox(
            "Auto-refresh (3s)",
            value=in_queue > 0 or ts_in_progress > 0,
        )
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

st.markdown("### Bulk Excel Export")
st.caption(
    "Build the current count workbooks for every analyzed video that already has counting lines. "
    "Videos without lines are skipped — draw lines on Count & Export first."
)

export_scope = st.radio(
    "Export scope",
    options=["current_folder", "all_analyzed"],
    format_func=lambda key: {
        "current_folder": f"Current folder only ({selected_row['analyzed']} analyzed)",
        "all_analyzed": f"All analyzed videos ({analyzed})",
    }[key],
    horizontal=True,
    key="bulk_xlsx_scope",
)
apply_ts_correction = st.checkbox(
    "Apply timestamp gap correction",
    value=False,
    help="Requires a completed timestamp scan per video. Videos without a gap map are skipped.",
    key="bulk_xlsx_corrected",
)

export_targets = (
    [v for v in folders[sel_folder] if v.get("status") == "analyzed"]
    if export_scope == "current_folder"
    else [v for v in videos if v.get("status") == "analyzed"]
)

ready_count = 0
for v in export_targets:
    try:
        if _cached_video_lines(str(v["id"])):
            ready_count += 1
    except api.APIError:
        pass

ex1, ex2, ex3 = st.columns([2, 2, 2])
ex1.metric("Analyzed in scope", len(export_targets))
ex2.metric("Ready to export", ready_count)
ex3.metric("Missing lines", max(0, len(export_targets) - ready_count))

build_col, download_col = st.columns([2, 2])
with build_col:
    build_disabled = ready_count == 0
    if st.button(
        f"Build {ready_count} workbook(s)",
        key="bulk_xlsx_build_btn",
        type="primary",
        disabled=build_disabled,
        use_container_width=True,
    ):
        progress = st.progress(0.0, text="Starting exports…")
        status_box = st.empty()

        def _on_export_progress(done: int, total: int, video: dict) -> None:
            progress.progress(
                done / max(total, 1),
                text=f"Exporting {done}/{total}: {video.get('filename', '')}",
            )
            status_box.caption(f"Building workbook for {video.get('filename', '')}")

        with st.spinner("Building Excel workbooks…"):
            zip_bytes, summary = _bulk_export_xlsx_zip(
                export_targets,
                apply_timestamp_correction=apply_ts_correction,
                progress_cb=_on_export_progress,
            )
        st.session_state["bulk_xlsx_zip"] = zip_bytes
        st.session_state["bulk_xlsx_summary"] = summary
        st.session_state["bulk_xlsx_label"] = (
            f"counts-{_safe_zip_part(Path(sel_folder).name if export_scope == 'current_folder' else 'all')}"
            f"{'-corrected' if apply_ts_correction else ''}.zip"
        )
        progress.empty()
        status_box.empty()
        if summary["exported"]:
            st.success(f"Built {summary['exported']} workbook(s).")
        else:
            st.warning("No workbooks were built.")
        if summary["skipped_no_lines"]:
            st.caption(
                f"Skipped {len(summary['skipped_no_lines'])} video(s) without counting lines."
            )
        if summary["failed"]:
            st.warning(f"{len(summary['failed'])} export(s) failed:")
            for item in summary["failed"][:5]:
                st.caption(f"{item.get('filename')}: {item.get('error')}")
            if len(summary["failed"]) > 5:
                st.caption(f"…and {len(summary['failed']) - 5} more")

with download_col:
    zip_payload = st.session_state.get("bulk_xlsx_zip")
    zip_label = st.session_state.get("bulk_xlsx_label", "counts-export.zip")
    st.download_button(
        "Download ZIP",
        data=zip_payload or b"",
        file_name=zip_label,
        mime="application/zip",
        disabled=not zip_payload,
        use_container_width=True,
        key="bulk_xlsx_download_btn",
    )
    last_summary = st.session_state.get("bulk_xlsx_summary")
    if last_summary and last_summary.get("files"):
        st.caption(f"Last build: {last_summary['exported']} file(s) in archive")

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
            seg_cache_key = f"segs_loaded_{v['id']}"
            with st.expander(label, expanded=False):
                if not st.session_state.get(seg_cache_key):
                    if st.button("Load segments", key=f"load_segs_{v['id']}", use_container_width=True):
                        st.session_state[seg_cache_key] = True
                        st.rerun()
                    segs = []
                else:
                    try:
                        segs = _cached_video_segments(v["id"])
                    except (api.APIError, httpx.TimeoutException, httpx.ReadTimeout):
                        st.warning("Could not load segment data (API busy or timed out).")
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

if auto_refresh and (in_queue > 0 or ts_in_progress > 0):
    time.sleep(3)
    st.rerun()
elif auto_refresh and ts_in_progress == 0 and in_queue == 0:
    time.sleep(10)
    st.rerun()
