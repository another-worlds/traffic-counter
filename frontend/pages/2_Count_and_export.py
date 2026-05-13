"""Count & Export page — rich overlay canvas editor."""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Dict, List, Optional

import httpx
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

import api_client as api
from sidebar import render_sidebar

st.set_page_config(page_title="Count & Export", page_icon="📏", layout="wide")
st.title("📏 Count & Export")

# ── Sidebar ───────────────────────────────────────────────────────────────────
ws = render_sidebar()
if not ws:
    st.warning("Pick a workspace in the sidebar to begin.")
    st.stop()

# ── Component declaration ─────────────────────────────────────────────────────
_COMPONENT_DIR = Path(__file__).parent.parent / "canvas_editor"
_canvas_editor = components.declare_component("canvas_editor", path=str(_COMPONENT_DIR))

# ── Session state init ────────────────────────────────────────────────────────
for _k, _v in [
    ("canvas_version", 0),
    ("counts", None),
    ("needs_recount", True),
]:
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ── Video selection ───────────────────────────────────────────────────────────
videos = [v for v in api.list_videos(ws["id"]) if v["status"] == "analyzed"]
if not videos:
    st.info("No analyzed videos yet. Analyze videos on the Videos page.")
    st.stop()

video_labels = {f'{v["filename"]} ({v["num_tracks"]} tracks)': v for v in videos}
default_pick = st.session_state.get("_vpicks", [list(video_labels.keys())[0]])
picks = st.multiselect(
    "Videos to count over",
    list(video_labels.keys()),
    default=[p for p in default_pick if p in video_labels],
    key="video_multiselect",
)
st.session_state["_vpicks"] = picks
selected = [video_labels[k] for k in picks]
if not selected:
    st.stop()

preview_video = selected[0]
video_ids = [v["id"] for v in selected]

# ── Load frame/overlay URLs ───────────────────────────────────────────────────
frame_url = api.get_frame_url(preview_video["id"])
traj_url = api.get_trajectories_url(preview_video["id"])
if not frame_url or not traj_url:
    st.error("Frame or trajectories missing — re-analyze the video.")
    st.stop()

hm_url = api.get_heatmap_url(preview_video["id"])


@st.cache_data(show_spinner=False)
def _get_dims(url: str):
    r = httpx.get(url, timeout=30)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content))
    return img.size  # (w, h)


src_w, src_h = _get_dims(api.file_url(frame_url))
canvas_w = min(1100, src_w)
scale = canvas_w / src_w
canvas_h = int(src_h * scale)

# ── Saved lines ───────────────────────────────────────────────────────────────
saved_lines = api.list_lines(ws["id"])


def _run_counts():
    if not saved_lines:
        st.session_state["counts"] = None
        st.session_state["needs_recount"] = False
        return
    try:
        result = api.compute_counts(ws["id"], video_ids, [ln["id"] for ln in saved_lines])
        st.session_state["counts"] = {r["line_id"]: r for r in result["per_line"]}
        st.session_state["counts"]["__total_tracks"] = result["total_unique_tracks"]
    except Exception:
        pass
    st.session_state["needs_recount"] = False


if st.session_state["needs_recount"] and saved_lines:
    _run_counts()

counts_by_id: Dict = st.session_state.get("counts") or {}

# ── Action handler ────────────────────────────────────────────────────────────
def _handle_action(action: dict) -> None:
    t = action.get("type")
    if t == "create":
        a, b = action["a"], action["b"]
        color = action.get("color", "#e24b4a")
        name = f"line {len(saved_lines) + 1}"
        api.create_line(ws["id"], name, a[0], a[1], b[0], b[1], color=color)
        st.session_state["needs_recount"] = True
        st.session_state["canvas_version"] += 1

    elif t == "move":
        a, b = action["a"], action["b"]
        api.update_line(action["line_id"], points={"a": a, "b": b})
        st.session_state["needs_recount"] = True

    elif t == "rename":
        api.update_line(action["line_id"], name=action["name"])

    elif t == "color":
        api.update_line(action["line_id"], color=action["color"])
        st.session_state["canvas_version"] += 1

    elif t == "delete":
        api.delete_line(action["line_id"])
        st.session_state["needs_recount"] = True
        st.session_state["canvas_version"] += 1

    elif t == "duplicate":
        orig = next((l for l in saved_lines if l["id"] == action["line_id"]), None)
        if orig:
            pa, pb = orig["points"]["a"], orig["points"]["b"]
            api.create_line(
                ws["id"],
                orig["name"] + " (copy)",
                pa[0] + 20, pa[1] + 20,
                pb[0] + 20, pb[1] + 20,
                color=orig["color"],
            )
            st.session_state["needs_recount"] = True
            st.session_state["canvas_version"] += 1


# ── Canvas component ──────────────────────────────────────────────────────────
action = _canvas_editor(
    image_url=api.file_url(frame_url),
    overlay_url=api.file_url(traj_url),
    heatmap_url=api.file_url(hm_url) if hm_url else None,
    lines=saved_lines,
    counts=counts_by_id,
    scale=scale,
    canvas_width=canvas_w,
    canvas_height=canvas_h,
    canvas_version=st.session_state["canvas_version"],
    key=f"canvas_{preview_video['id']}_v{st.session_state['canvas_version']}",
    default=None,
    height=canvas_h + 8,
)

if action and isinstance(action, dict) and action.get("type") not in (None, "ready"):
    _handle_action(action)
    st.rerun()

# ── Below-canvas controls ─────────────────────────────────────────────────────
st.divider()
col1, col2, col3, col4 = st.columns(4)

# ── Recompute counts ──────────────────────────────────────────────────────────
with col1:
    if st.button("🔁 Recompute counts", use_container_width=True, type="primary"):
        with st.spinner("Computing…"):
            line_ids = [ln["id"] for ln in saved_lines]
            try:
                result = api.compute_counts(ws["id"], video_ids, line_ids)
                st.session_state["counts"] = {r["line_id"]: r for r in result["per_line"]}
                st.session_state["counts"]["__total_tracks"] = result["total_unique_tracks"]
                st.session_state["needs_recount"] = False
            except Exception as e:
                st.error(f"Count failed: {e}")
        st.rerun()

# ── Suggest lines ─────────────────────────────────────────────────────────────
with col2:
    if st.button("💡 Suggest lines", use_container_width=True):
        with st.spinner("Suggesting…"):
            try:
                suggestions = api.suggest_lines(ws["id"], video_ids)
                for s in suggestions:
                    api.create_line(
                        ws["id"],
                        s["name"],
                        s["points"]["a"][0], s["points"]["a"][1],
                        s["points"]["b"][0], s["points"]["b"][1],
                        color=s.get("color", "#4ecdc4"),
                    )
                st.session_state["needs_recount"] = True
                st.session_state["canvas_version"] += 1
                st.toast(f"Added {len(suggestions)} suggested line(s).", icon="💡")
            except Exception as e:
                st.error(f"Suggest failed: {e}")
        st.rerun()

# ── Presets ───────────────────────────────────────────────────────────────────
with col3:
    ref_w = preview_video.get("width") or src_w
    ref_h = preview_video.get("height") or src_h
    presets = [
        ("H-midline", (0, ref_h / 2), (ref_w, ref_h / 2), "#4ecdc4"),
        ("V-midline", (ref_w / 2, 0), (ref_w / 2, ref_h), "#f7b731"),
        ("Diag ↗",   (0, ref_h),      (ref_w, 0),          "#a29bfe"),
        ("Diag ↘",   (0, 0),          (ref_w, ref_h),      "#fd79a8"),
    ]
    with st.expander("📐 Presets"):
        for name, pa, pb, color in presets:
            if st.button(name, key=f"preset_{name}", use_container_width=True):
                api.create_line(ws["id"], name, pa[0], pa[1], pb[0], pb[1], color=color)
                st.session_state["needs_recount"] = True
                st.session_state["canvas_version"] += 1
                st.rerun()

# ── Import / Export lines JSON ────────────────────────────────────────────────
with col4:
    with st.expander("⚙ Import / Export lines"):
        lines_json = json.dumps(
            [
                {
                    "name": ln["name"],
                    "color": ln["color"],
                    "a": ln["points"]["a"],
                    "b": ln["points"]["b"],
                }
                for ln in saved_lines
            ],
            indent=2,
        )
        st.download_button(
            "📤 Export lines JSON",
            data=lines_json,
            file_name=f"lines-{ws['name'].replace(' ', '_')}.json",
            mime="application/json",
            use_container_width=True,
        )
        uploaded = st.file_uploader("Import lines JSON", type="json", key="import_lines")
        if uploaded:
            try:
                incoming = json.load(uploaded)
                added = 0
                for ln in incoming:
                    api.create_line(
                        ws["id"],
                        ln["name"],
                        ln["a"][0], ln["a"][1],
                        ln["b"][0], ln["b"][1],
                        color=ln.get("color", "#e24b4a"),
                    )
                    added += 1
                st.session_state["needs_recount"] = True
                st.session_state["canvas_version"] += 1
                st.toast(f"Imported {added} line(s).", icon="✅")
                st.rerun()
            except Exception as e:
                st.error(f"Import failed: {e}")

# ── Counts summary table ──────────────────────────────────────────────────────
fresh_counts: Dict = st.session_state.get("counts") or {}
if fresh_counts and saved_lines:
    st.divider()
    total_tracks = fresh_counts.get("__total_tracks", 0)
    per_line_list = [fresh_counts[ln["id"]] for ln in saved_lines if ln["id"] in fresh_counts]

    if per_line_list:
        mc1, mc2, mc3, mc4 = st.columns(4)
        mc1.metric("Unique tracks (video)", total_tracks)
        best = max(per_line_list, key=lambda r: r["total"])
        mc2.metric("Top line", best["line_name"])
        mc3.metric("Top count", best["total"])
        total_cls: Dict[str, int] = {}
        for r in per_line_list:
            for cls, cnt in r["by_class"].items():
                total_cls[cls] = total_cls.get(cls, 0) + cnt
        if total_cls:
            dom_cls = max(total_cls, key=total_cls.get)
            mc4.metric("Dominant class", f"{dom_cls} ({total_cls[dom_cls]})")

        rows = []
        for ln in saved_lines:
            r = fresh_counts.get(ln["id"])
            if not r:
                continue
            rows.append({
                "Line": r["line_name"],
                "Count": r["total"],
                "% of video": f'{r["percent_of_video_total"]:.1f}',
                "% of lines": f'{r["percent_of_drawn_lines"]:.1f}',
                "Dir ▲": r["by_direction"].get("positive", 0),
                "Dir ▼": r["by_direction"].get("negative", 0),
                "Car": r["by_class"].get("car", 0),
                "Truck": r["by_class"].get("truck", 0),
                "Bus": r["by_class"].get("bus", 0),
                "Motorcycle": r["by_class"].get("motorcycle", 0),
                "Bicycle": r["by_class"].get("bicycle", 0),
            })
        if rows:
            st.dataframe(rows, use_container_width=True, hide_index=True)

# ── Export xlsx ───────────────────────────────────────────────────────────────
st.divider()
st.subheader("Export to Excel")
if st.button("Generate xlsx", type="secondary"):
    line_ids = [ln["id"] for ln in saved_lines]
    if not line_ids:
        st.error("No saved lines to export.")
    else:
        with st.spinner("Building workbook…"):
            data = api.export_xlsx(ws["id"], video_ids, line_ids)
        st.download_button(
            "📥 Download counts.xlsx",
            data=data,
            file_name=f"counts-{ws['name'].replace(' ', '_')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
