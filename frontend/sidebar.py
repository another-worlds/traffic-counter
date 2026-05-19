"""
Shared sidebar module — imported by every page.

Renders the workspace selector, stats, per-video strip, and contextual
actions into st.sidebar. Returns the currently selected workspace dict
(or None if nothing is selected).

Usage:
    from sidebar import render_sidebar
    ws = render_sidebar()
    if not ws:
        st.stop()
"""
from __future__ import annotations
import math
import os
from datetime import datetime, timezone
from typing import Optional

import streamlit as st
import api_client as api


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _workspace_status(summary: dict) -> tuple[str, str]:
    """Return (key, label) status pill from a workspace summary dict."""
    total = summary.get("total_videos", 0)
    analyzed = summary.get("analyzed_videos", 0)
    active = summary.get("queued_or_analyzing", 0)
    errors = summary.get("error_videos", 0)
    lines = summary.get("lines_count", 0)
    exported = summary.get("last_exported_at")

    if total == 0:
        return "empty", "⚪ Empty"
    if active > 0:
        return "analyzing", "🟡 Analyzing…"
    if errors > 0 and analyzed == 0:
        return "error", "🔴 Error"
    if exported:
        return "exported", "📦 Exported"
    if analyzed > 0 and lines > 0:
        return "ready", "🟢 Ready to export"
    if analyzed > 0:
        return "analyzed", "🔵 Analyzed"
    return "uploading", "🔵 Uploaded"


def _fmt_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "—"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def _fmt_size(bytes_val: Optional[int]) -> str:
    if not bytes_val:
        return "—"
    if bytes_val < 1024 ** 2:
        return f"{bytes_val / 1024:.0f} KB"
    if bytes_val < 1024 ** 3:
        return f"{bytes_val / 1024**2:.1f} MB"
    return f"{bytes_val / 1024**3:.2f} GB"


# ---------------------------------------------------------------------------
# Main render function
# ---------------------------------------------------------------------------

def render_sidebar() -> Optional[dict]:
    """Render the full sidebar. Returns the selected workspace dict or None."""

    with st.sidebar:
        st.header("🗂️ Workspaces")

        # --- workspace list ---
        try:
            workspaces = api.list_projects()
        except Exception as e:
            st.error(f"API unreachable: {e}")
            st.stop()

        ws_map = {w["name"]: w for w in workspaces}

        current = st.session_state.get("workspace")
        current_name = current["name"] if current else None
        options = ["— select —"] + list(ws_map.keys())
        current_idx = options.index(current_name) if current_name in options else 0

        selected_name = st.selectbox(
            "Active workspace",
            options=options,
            index=current_idx,
            label_visibility="collapsed",
            key="sidebar_ws_select",
        )

        if selected_name and selected_name != "— select —":
            ws = ws_map[selected_name]
            st.session_state["workspace"] = ws
        else:
            st.session_state.pop("workspace", None)
            ws = None

        # --- new workspace form ---
        with st.expander("＋ New workspace"):
            with st.form("create_ws_form", clear_on_submit=True):
                name = st.text_input("Name")
                desc = st.text_area("Description (optional)", height=60)
                if st.form_submit_button("Create") and name.strip():
                    try:
                        new_ws = api.create_project(name.strip(), desc.strip())
                        st.session_state["workspace"] = new_ws
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))

        if not ws:
            return None

        st.divider()

        # --- summary stats ---
        try:
            summary = api.workspace_summary(ws["id"])
        except Exception:
            summary = {}

        _, status_label = _workspace_status(summary)
        st.caption(status_label)

        c1, c2 = st.columns(2)
        c1.metric("Videos", summary.get("total_videos", 0))
        c2.metric("Analyzed", summary.get("analyzed_videos", 0))
        c1.metric("Duration", _fmt_duration(summary.get("total_duration_s")))
        c2.metric("Lines", summary.get("lines_count", 0))

        size_str = _fmt_size(summary.get("total_size_bytes"))
        if size_str != "—":
            st.caption(f"Storage: {size_str}")

        if summary.get("last_exported_at"):
            try:
                exp_dt = datetime.fromisoformat(summary["last_exported_at"].replace("Z", "+00:00"))
                st.caption(f"Last export: {exp_dt.strftime('%b %d %H:%M')}")
            except Exception:
                pass

        # --- per-video strip ---
        try:
            videos = api.list_videos(ws["id"])
        except Exception:
            videos = []

        if videos:
            badge_map = {
                "uploaded": "🟦", "queued": "🟨", "analyzing": "🟧",
                "analyzed": "🟩", "error": "🟥",
            }
            with st.expander(f"Videos ({len(videos)})"):
                for v in videos:
                    badge = badge_map.get(v["status"], "⬜")
                    dur = _fmt_duration(v.get("duration_s"))
                    st.write(f"{badge} **{v['filename']}** — {dur}")
                    if v["status"] == "analyzing" and v.get("progress_pct") is not None:
                        st.progress(v["progress_pct"])

        st.divider()

        # --- contextual actions ---
        analyzed_ids = [v["id"] for v in videos if v["status"] == "analyzed"]

        try:
            lines = api.list_lines(ws["id"])
        except Exception:
            lines = []

        if analyzed_ids and lines:
            with st.expander("📊 Quick export"):
                if st.button("Export all analyzed videos", use_container_width=True, key="sidebar_export_btn"):
                    try:
                        data = api.export_xlsx(ws["id"], analyzed_ids, [ln["id"] for ln in lines])
                        fname = f"counts-{ws['name'].replace(' ', '_')}.xlsx"
                        st.download_button(
                            "💾 Download XLSX",
                            data=data,
                            file_name=fname,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True,
                            key="sidebar_dl_btn",
                        )
                    except Exception as e:
                        st.error(str(e))

        # delete (danger zone) — hidden during public test. Set
        # ENABLE_WORKSPACE_DELETE=1 in the frontend env to bring it back.
        if os.environ.get("ENABLE_WORKSPACE_DELETE", "").strip().lower() in {"1", "true", "yes"}:
            confirm_key = f"confirm_del_{ws['id']}"
            if st.button("🗑️ Delete workspace", type="secondary", use_container_width=True):
                if st.session_state.get(confirm_key):
                    try:
                        api.delete_project(ws["id"])
                        st.session_state.pop("workspace", None)
                        st.session_state.pop(confirm_key, None)
                        st.rerun()
                    except Exception as e:
                        st.error(str(e))
                else:
                    st.session_state[confirm_key] = True

            if st.session_state.get(confirm_key):
                st.warning("⚠️ Click again to confirm deletion — all videos and lines will be lost.")

    return st.session_state.get("workspace")
