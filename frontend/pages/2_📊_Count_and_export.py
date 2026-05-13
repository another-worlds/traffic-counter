"""
Count & Export page — enhanced line editor.

Features:
  ✏️  Draw mode   – click-drag to draw new counting lines with a colour picker.
  ✋  Edit mode   – select any saved line on the canvas and drag its endpoints.
  📊  Live counts – every line shows its trajectory count, direction split and
                    dominant vehicle class; recomputed in the background after
                    every change.
  ✨  Suggest     – automatic line placement based on trajectory-density analysis.
  📐  Presets     – horizontal/vertical/diagonal midlines created in one click.
  📥/📤 Import/Export – JSON-based line config for portability across workspaces.
  📊  Export XLSX – full counts workbook download.
"""
from __future__ import annotations

import io
import json
import math
import time
import colorsys
from typing import Dict, List, Optional, Tuple

import httpx
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from streamlit_drawable_canvas import st_canvas

import api_client as api
from sidebar import render_sidebar

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="Count & Export", page_icon="📏", layout="wide")
st.title("📏 Count & Export")

ws = render_sidebar()
if not ws:
    st.warning("Pick a workspace in the sidebar to begin.")
    st.stop()

st.caption(f"Workspace: **{ws['name']}**")

# ── Session-state initialisation ─────────────────────────────────────────────
if "canvas_version" not in st.session_state:
    st.session_state["canvas_version"] = 0
if "drawing_color" not in st.session_state:
    st.session_state["drawing_color"] = "#e24b4a"
if "canvas_mode" not in st.session_state:
    st.session_state["canvas_mode"] = "draw"
if "counts" not in st.session_state:
    st.session_state["counts"] = None
if "needs_recount" not in st.session_state:
    st.session_state["needs_recount"] = True
if "suggest_results" not in st.session_state:
    st.session_state["suggest_results"] = None
if "suggest_open" not in st.session_state:
    st.session_state["suggest_open"] = False

# ── Video selection ───────────────────────────────────────────────────────────
videos = [v for v in api.list_videos(ws["id"]) if v["status"] == "analyzed"]
if not videos:
    st.info("No analyzed videos in this workspace yet — analyze one first.")
    st.stop()

video_labels = {f'{v["filename"]} ({v["num_tracks"]} tracks)': v for v in videos}
default_picks = [list(video_labels.keys())[0]]
picks = st.multiselect(
    "Videos to count over",
    options=list(video_labels.keys()),
    default=st.session_state.get("selected_video_labels", default_picks),
    help="Counting is computed over the union of all selected videos.",
    key="video_multiselect",
)
selected = [video_labels[k] for k in picks]
st.session_state["selected_video_labels"] = picks
if not selected:
    st.stop()

preview_video = selected[0]
video_ids = [v["id"] for v in selected]

# ── Load background image ─────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def fetch_image(url: str) -> Image.Image:
    r = httpx.get(url, timeout=30.0)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGBA")

with st.spinner("Loading frame…"):
    frame_url = api.get_frame_url(preview_video["id"])
    traj_url = api.get_trajectories_url(preview_video["id"])
    if not frame_url or not traj_url:
        st.error("Frame or trajectories overlay missing — re-analyze the video.")
        st.stop()
    bg = fetch_image(api.file_url(frame_url))
    overlay = fetch_image(api.file_url(traj_url))

composite = Image.alpha_composite(bg, overlay.resize(bg.size))
src_w, src_h = composite.size
canvas_w = min(900, src_w)
scale = canvas_w / src_w
canvas_h = int(src_h * scale)

# ── Saved lines ───────────────────────────────────────────────────────────────
saved_lines = api.list_lines(ws["id"])


# ── Auto-count helper ─────────────────────────────────────────────────────────
def _run_counts_silently():
    """Recompute counts for all saved lines. Updates session_state['counts']."""
    if not saved_lines:
        st.session_state["counts"] = None
        return
    line_ids = [ln["id"] for ln in saved_lines]
    try:
        result = api.compute_counts(ws["id"], video_ids, line_ids)
        st.session_state["counts"] = {r["line_id"]: r for r in result["per_line"]}
        st.session_state["counts"]["__total_tracks"] = result["total_unique_tracks"]
    except Exception:
        pass  # keep stale counts rather than crash the page
    st.session_state["needs_recount"] = False


if st.session_state["needs_recount"] and saved_lines:
    _run_counts_silently()

counts_by_id: Dict = st.session_state.get("counts") or {}


# ── Background composer: bake saved lines + labels into a PIL image ───────────
def _bake_lines_on_image(
    img: Image.Image,
    lines: List[Dict],
    scale: float,
) -> Image.Image:
    """Return a copy of *img* with each saved line drawn on it."""
    out = img.copy().convert("RGBA")
    draw = ImageDraw.Draw(out)
    for ln in lines:
        a = ln["points"]["a"]
        b = ln["points"]["b"]
        ax, ay = int(a[0] * scale), int(a[1] * scale)
        bx, by = int(b[0] * scale), int(b[1] * scale)
        color = ln.get("color", "#e24b4a")
        draw.line([(ax, ay), (bx, by)], fill=color, width=3)
        # End-point handles
        for px, py in [(ax, ay), (bx, by)]:
            r = 6
            draw.ellipse([(px - r, py - r), (px + r, py + r)], fill=color, outline="white", width=2)
        # Label (name + count)
        cx_label = (ax + bx) // 2 + 6
        cy_label = (ay + by) // 2 - 12
        c_info = counts_by_id.get(ln["id"])
        count_str = f" [{c_info['total']}]" if c_info else ""
        label = f"{ln['name']}{count_str}"
        # Shadow
        draw.text((cx_label + 1, cy_label + 1), label, fill=(0, 0, 0, 180))
        draw.text((cx_label, cy_label), label, fill=color)
    return out


canvas_bg = composite.resize((canvas_w, canvas_h))


# ── Fabric.js object helpers ──────────────────────────────────────────────────
def _line_endpoints_from_fabric(obj: dict) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """Extract (A, B) canvas-pixel coords from a fabric.js Line JSON object."""
    left = float(obj.get("left", 0))
    top = float(obj.get("top", 0))
    w = float(obj.get("width", 0))
    h = float(obj.get("height", 0))
    x1, x2 = float(obj.get("x1", 0)), float(obj.get("x2", w))
    y1, y2 = float(obj.get("y1", 0)), float(obj.get("y2", h))
    ax = left if x1 <= x2 else left + w
    bx = left + w if x1 <= x2 else left
    ay = top if y1 <= y2 else top + h
    by = top + h if y1 <= y2 else top
    return (ax, ay), (bx, by)


def _saved_lines_to_fabric(lines: List[Dict], scale: float) -> dict:
    """Build an ``initial_drawing`` JSON blob for st_canvas from saved lines."""
    objects = []
    for ln in lines:
        a = ln["points"]["a"]
        b = ln["points"]["b"]
        ax, ay = a[0] * scale, a[1] * scale
        bx, by = b[0] * scale, b[1] * scale
        objects.append({
            "type": "line",
            "version": "5.3.0",
            "originX": "left",
            "originY": "top",
            "left": min(ax, bx),
            "top": min(ay, by),
            "width": abs(bx - ax),
            "height": abs(by - ay),
            "x1": 0 if ax <= bx else abs(bx - ax),
            "y1": 0 if ay <= by else abs(by - ay),
            "x2": abs(bx - ax) if ax <= bx else 0,
            "y2": abs(by - ay) if ay <= by else 0,
            "stroke": ln.get("color", "#e24b4a"),
            "strokeWidth": 3,
            "selectable": True,
            "evented": True,
            # Custom field — preserved by the canvas JSON roundtrip
            "_line_id": ln["id"],
        })
    return {"version": "5.3.0", "objects": objects}


# ── Layout: two columns ───────────────────────────────────────────────────────
col_canvas, col_panel = st.columns([3, 2])

# ─────────────────────────── LEFT: canvas ────────────────────────────────────
with col_canvas:
    # ── Heatmap toggle ────────────────────────────────────────────────────────
    hm_col, mode_col = st.columns([1, 2])
    with hm_col:
        show_heatmap = st.toggle(
            "🌡️ Heatmap",
            value=st.session_state.get("show_heatmap", False),
            key="heatmap_toggle",
            help="Overlay a track-density heatmap. Generated on first use and cached.",
        )
        st.session_state["show_heatmap"] = show_heatmap

    if show_heatmap:
        hm_url = api.get_heatmap_url(preview_video["id"])
        if hm_url:
            with st.spinner("Loading heatmap…"):
                hm_img = fetch_image(api.file_url(hm_url))
            hm_resized = hm_img.resize((canvas_w, canvas_h)).convert("RGBA")
            canvas_bg = Image.alpha_composite(canvas_bg.convert("RGBA"), hm_resized)
        else:
            with hm_col:
                st.caption("Not ready")

    # Mode toggle
    with mode_col:
        mode_choice = st.radio(
            "Mode",
            options=["✏️ Draw", "✋ Edit"],
            horizontal=True,
            key="mode_radio",
            label_visibility="collapsed",
        )
    new_mode = "draw" if mode_choice == "✏️ Draw" else "edit"
    if new_mode != st.session_state["canvas_mode"]:
        st.session_state["canvas_mode"] = new_mode
        # Reset canvas when switching modes so fabric state is clean
        st.session_state["canvas_version"] += 1

    is_draw = st.session_state["canvas_mode"] == "draw"

    if is_draw:
        col_color, col_hint = st.columns([1, 4])
        with col_color:
            new_color = st.color_picker(
                "Line color",
                value=st.session_state["drawing_color"],
                key="color_picker",
                label_visibility="collapsed",
            )
            if new_color != st.session_state["drawing_color"]:
                st.session_state["drawing_color"] = new_color
        with col_hint:
            st.caption("Click and drag to draw a counting line.")

        bg_with_lines = _bake_lines_on_image(canvas_bg, saved_lines, 1.0)
        canvas_result = st_canvas(
            fill_color="rgba(0,0,0,0)",
            stroke_width=3,
            stroke_color=st.session_state["drawing_color"],
            background_image=bg_with_lines,
            update_streamlit=True,
            height=canvas_h,
            width=canvas_w,
            drawing_mode="line",
            key=f"canvas_draw_{preview_video['id']}_v{st.session_state['canvas_version']}",
        )

        # ── New-line save form ──
        drawn = []
        if canvas_result.json_data and canvas_result.json_data.get("objects"):
            for obj in canvas_result.json_data["objects"]:
                if obj.get("type") == "line":
                    (ax, ay), (bx, by) = _line_endpoints_from_fabric(obj)
                    drawn.append({
                        "a": (ax / scale, ay / scale),
                        "b": (bx / scale, by / scale),
                        "a_canvas": (ax, ay),
                        "b_canvas": (bx, by),
                    })

        if drawn:
            st.markdown("**New lines** — name them and save:")
            for i, d in enumerate(drawn):
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 2, 1])
                    with c1:
                        st.caption(
                            f"({d['a'][0]:.0f}, {d['a'][1]:.0f}) → "
                            f"({d['b'][0]:.0f}, {d['b'][1]:.0f})"
                        )
                    with c2:
                        name_val = st.text_input(
                            "Name",
                            value=f"line {len(saved_lines) + i + 1}",
                            key=f"new_name_{i}",
                            label_visibility="collapsed",
                        )
                    with c3:
                        if st.button("Save", key=f"save_new_{i}", type="primary"):
                            api.create_line(
                                ws["id"], name_val,
                                d["a"][0], d["a"][1], d["b"][0], d["b"][1],
                                color=st.session_state["drawing_color"],
                            )
                            st.session_state["canvas_version"] += 1
                            st.session_state["needs_recount"] = True
                            st.toast(f"Line **{name_val}** saved!", icon="✅")
                            st.rerun()

    else:  # Edit mode
        st.caption(
            "Select a line on the canvas, drag its endpoints to reposition it, "
            "then click **Apply edits**."
        )
        initial_drawing = _saved_lines_to_fabric(saved_lines, scale)
        canvas_result = st_canvas(
            fill_color="rgba(0,0,0,0)",
            stroke_width=3,
            stroke_color="#ffffff",
            background_image=canvas_bg,
            initial_drawing=initial_drawing,
            update_streamlit=True,
            height=canvas_h,
            width=canvas_w,
            drawing_mode="transform",
            key=f"canvas_edit_{preview_video['id']}_v{st.session_state['canvas_version']}",
        )

        c_apply, c_refresh = st.columns(2)
        with c_apply:
            if st.button("✅ Apply edits", use_container_width=True, type="primary"):
                if canvas_result.json_data and canvas_result.json_data.get("objects"):
                    updated = 0
                    # Build lookup: line_id -> current DB points (canvas coords)
                    db_pts = {
                        ln["id"]: (
                            (ln["points"]["a"][0] * scale, ln["points"]["a"][1] * scale),
                            (ln["points"]["b"][0] * scale, ln["points"]["b"][1] * scale),
                        )
                        for ln in saved_lines
                    }
                    for obj in canvas_result.json_data["objects"]:
                        if obj.get("type") != "line":
                            continue
                        lid = obj.get("_line_id")
                        if not lid:
                            continue
                        (ax, ay), (bx, by) = _line_endpoints_from_fabric(obj)
                        orig = db_pts.get(lid)
                        if orig is None:
                            continue
                        # Only push update if coordinates changed meaningfully (>1px)
                        (oax, oay), (obx, oby) = orig
                        if (
                            abs(ax - oax) > 1 or abs(ay - oay) > 1
                            or abs(bx - obx) > 1 or abs(by - oby) > 1
                        ):
                            api.update_line(
                                lid,
                                points={
                                    "a": [ax / scale, ay / scale],
                                    "b": [bx / scale, by / scale],
                                },
                            )
                            updated += 1
                    if updated:
                        st.session_state["canvas_version"] += 1
                        st.session_state["needs_recount"] = True
                        st.toast(f"Updated {updated} line(s)!", icon="✅")
                        st.rerun()
                    else:
                        st.toast("No changes detected.", icon="ℹ️")
                else:
                    st.toast("Canvas empty — nothing to apply.", icon="ℹ️")

        with c_refresh:
            if st.button("🔄 Refresh canvas", use_container_width=True):
                st.session_state["canvas_version"] += 1
                st.rerun()

    # ── Presets & Import/Export ───────────────────────────────────────────────
    with st.expander("📐 Quick presets"):
        ref_w = preview_video.get("width") or src_w
        ref_h = preview_video.get("height") or src_h
        preset_cols = st.columns(4)
        presets = [
            ("H-midline", (0, ref_h / 2), (ref_w, ref_h / 2), "#4ecdc4"),
            ("V-midline", (ref_w / 2, 0), (ref_w / 2, ref_h), "#f7b731"),
            ("Diag ↗", (0, ref_h), (ref_w, 0), "#a29bfe"),
            ("Diag ↘", (0, 0), (ref_w, ref_h), "#fd79a8"),
        ]
        for col, (label, pa, pb, clr) in zip(preset_cols, presets):
            if col.button(label, use_container_width=True):
                api.create_line(
                    ws["id"], label,
                    pa[0], pa[1], pb[0], pb[1],
                    color=clr,
                )
                st.session_state["canvas_version"] += 1
                st.session_state["needs_recount"] = True
                st.toast(f"Preset **{label}** added!", icon="📐")
                st.rerun()

    with st.expander("📥📤 Import / Export lines"):
        # Export
        if saved_lines:
            export_payload = [
                {"name": ln["name"], "color": ln.get("color", "#e24b4a"), "points": ln["points"]}
                for ln in saved_lines
            ]
            st.download_button(
                "💾 Export lines JSON",
                data=json.dumps(export_payload, indent=2),
                file_name=f"lines-{ws['name'].replace(' ', '_')}.json",
                mime="application/json",
                use_container_width=True,
            )
        # Import
        uploaded = st.file_uploader(
            "Upload lines JSON",
            type="json",
            label_visibility="collapsed",
            key="lines_import",
        )
        if uploaded is not None:
            try:
                payload = json.load(uploaded)
                if st.button("✅ Create imported lines", use_container_width=True):
                    for item in payload:
                        pts = item["points"]
                        api.create_line(
                            ws["id"],
                            item.get("name", "imported line"),
                            pts["a"][0], pts["a"][1],
                            pts["b"][0], pts["b"][1],
                            color=item.get("color", "#e24b4a"),
                        )
                    st.session_state["canvas_version"] += 1
                    st.session_state["needs_recount"] = True
                    st.toast(f"Imported {len(payload)} line(s)!", icon="📥")
                    st.rerun()
            except Exception as e:
                st.error(f"Invalid JSON: {e}")


# ──────────────────────────── RIGHT: line panel ───────────────────────────────
with col_panel:
    st.subheader("Counting lines")

    # ── Suggest-lines section ─────────────────────────────────────────────────
    s_col1, s_col2 = st.columns([3, 1])
    with s_col1:
        n_suggest = st.number_input("Suggestions to generate", 1, 10, 3, key="n_suggest")
    with s_col2:
        st.write("")  # vertical alignment spacer
        st.write("")
        do_suggest = st.button("✨ Suggest", use_container_width=True)

    if do_suggest:
        with st.spinner("Analysing trajectories…"):
            try:
                st.session_state["suggest_results"] = api.suggest_lines(
                    ws["id"], video_ids, n=int(n_suggest)
                )
                st.session_state["suggest_open"] = True
            except Exception as e:
                st.error(f"Suggestion failed: {e}")

    if st.session_state["suggest_results"]:
        with st.expander(
            f"✨ {len(st.session_state['suggest_results'])} suggestion(s)",
            expanded=st.session_state["suggest_open"],
        ):
            for sug in st.session_state["suggest_results"]:
                with st.container(border=True):
                    sc1, sc2, sc3 = st.columns([3, 2, 1])
                    with sc1:
                        st.markdown(
                            f'<span style="color:{sug["color"]}">■</span> '
                            f'**{sug["name"]}** — score {sug["score"]} tracks',
                            unsafe_allow_html=True,
                        )
                        a, b = sug["points"]["a"], sug["points"]["b"]
                        st.caption(
                            f"({a[0]:.0f}, {a[1]:.0f}) → ({b[0]:.0f}, {b[1]:.0f})"
                        )
                    with sc2:
                        name_input = st.text_input(
                            "Name",
                            value=sug["name"],
                            key=f"sug_name_{sug['name']}",
                            label_visibility="collapsed",
                        )
                    with sc3:
                        if st.button("Add", key=f"sug_add_{sug['name']}"):
                            pts = sug["points"]
                            api.create_line(
                                ws["id"], name_input,
                                pts["a"][0], pts["a"][1],
                                pts["b"][0], pts["b"][1],
                                color=sug["color"],
                            )
                            st.session_state["canvas_version"] += 1
                            st.session_state["needs_recount"] = True
                            st.toast(f"Line **{name_input}** added!", icon="✅")
                            st.rerun()

    st.divider()

    # ── Saved-line list ───────────────────────────────────────────────────────
    if not saved_lines:
        st.caption("No lines yet — draw one on the canvas or use a preset.")
    else:
        total_tracks = counts_by_id.get("__total_tracks", 0)
        for ln in saved_lines:
            cinfo = counts_by_id.get(ln["id"])
            count_val = cinfo["total"] if cinfo else None

            with st.container(border=True):
                # Header row: colour dot, editable name, count badge, delete
                h1, h2, h3, h4 = st.columns([0.4, 3, 1.2, 0.5])
                with h1:
                    st.markdown(
                        f'<div style="width:18px;height:18px;border-radius:50%;'
                        f'background:{ln["color"]};margin-top:8px"></div>',
                        unsafe_allow_html=True,
                    )
                with h2:
                    new_name = st.text_input(
                        "Line name",
                        value=ln["name"],
                        key=f"name_{ln['id']}",
                        label_visibility="collapsed",
                    )
                    if new_name != ln["name"]:
                        api.update_line(ln["id"], name=new_name)
                        st.session_state["needs_recount"] = True
                        st.toast(f"Renamed to **{new_name}**", icon="✏️")
                        st.rerun()
                with h3:
                    if count_val is not None:
                        st.metric(
                            label="count",
                            value=count_val,
                            label_visibility="collapsed",
                        )
                    else:
                        st.caption("—")
                with h4:
                    with st.popover("🗑", use_container_width=True):
                        st.write(f"Delete **{ln['name']}**?")
                        if st.button("Confirm delete", key=f"confirm_del_{ln['id']}", type="primary"):
                            api.delete_line(ln["id"])
                            st.session_state["canvas_version"] += 1
                            st.session_state["needs_recount"] = True
                            st.toast(f"Line **{ln['name']}** deleted.", icon="🗑️")
                            st.rerun()

                # Colour picker row
                cc1, cc2 = st.columns([1, 3])
                with cc1:
                    new_color = st.color_picker(
                        "Color",
                        value=ln["color"],
                        key=f"color_{ln['id']}",
                        label_visibility="collapsed",
                    )
                    if new_color != ln["color"]:
                        api.update_line(ln["id"], color=new_color)
                        st.session_state["canvas_version"] += 1
                        st.session_state["needs_recount"] = True
                        st.toast("Color updated!", icon="🎨")
                        st.rerun()

                # Count breakdown
                if cinfo:
                    dir_pos = cinfo["by_direction"].get("positive", 0)
                    dir_neg = cinfo["by_direction"].get("negative", 0)
                    top_cls = (
                        max(cinfo["by_class"], key=cinfo["by_class"].get)
                        if cinfo["by_class"]
                        else "—"
                    )
                    top_cls_count = cinfo["by_class"].get(top_cls, 0)
                    pct = cinfo.get("percent_of_video_total", 0.0)
                    with cc2:
                        st.caption(
                            f"▲ {dir_pos}  ▼ {dir_neg}  |  "
                            f"top: {top_cls} ({top_cls_count})  |  "
                            f"{pct:.1f}% of video"
                        )
                else:
                    with cc2:
                        st.caption("Counts not yet computed.")

                # Coords display
                a, b = ln["points"]["a"], ln["points"]["b"]
                with st.expander("coords", expanded=False):
                    st.caption(
                        f"A ({a[0]:.0f}, {a[1]:.0f})  →  B ({b[0]:.0f}, {b[1]:.0f})"
                    )
                    # Duplicate button
                    if st.button("Duplicate +20px", key=f"dup_{ln['id']}"):
                        api.create_line(
                            ws["id"],
                            f"{ln['name']} copy",
                            a[0] + 20, a[1] + 20,
                            b[0] + 20, b[1] + 20,
                            color=ln["color"],
                        )
                        st.session_state["canvas_version"] += 1
                        st.session_state["needs_recount"] = True
                        st.toast("Line duplicated!", icon="📋")
                        st.rerun()

# ── Divider ───────────────────────────────────────────────────────────────────
st.divider()

# ── Stats summary ─────────────────────────────────────────────────────────────
if counts_by_id and saved_lines:
    total_tracks = counts_by_id.get("__total_tracks", 0)
    per_line_list = [counts_by_id[ln["id"]] for ln in saved_lines if ln["id"] in counts_by_id]
    if per_line_list:
        st.subheader("Summary")
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

        # Per-class bar breakdown (horizontal proportional bar)
        if total_cls:
            total_cls_sum = sum(total_cls.values())
            cls_md = " | ".join(
                f"**{k}** {v} ({100*v/total_cls_sum:.0f}%)"
                for k, v in sorted(total_cls.items(), key=lambda x: -x[1])
            )
            st.caption(cls_md)

# ── Counts table ──────────────────────────────────────────────────────────────
st.subheader("Counts")

if not saved_lines:
    st.info("Add at least one counting line to compute counts.")
    st.stop()

if st.button("🔁 Recompute counts", type="primary"):
    with st.spinner("Computing…"):
        line_ids = [ln["id"] for ln in saved_lines]
        try:
            result = api.compute_counts(ws["id"], video_ids, line_ids)
            st.session_state["counts"] = {r["line_id"]: r for r in result["per_line"]}
            st.session_state["counts"]["__total_tracks"] = result["total_unique_tracks"]
            st.session_state["needs_recount"] = False
        except Exception as e:
            st.error(f"Count failed: {e}")
            st.stop()
    st.rerun()

counts_by_id_fresh = st.session_state.get("counts") or {}
if counts_by_id_fresh:
    rows = []
    for ln in saved_lines:
        r = counts_by_id_fresh.get(ln["id"])
        if not r:
            continue
        rows.append({
            "Line": r["line_name"],
            "Count": r["total"],
            "% of video": r["percent_of_video_total"],
            "% of lines": r["percent_of_drawn_lines"],
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
else:
    st.caption("Click **Recompute counts** above to update the table.")

# ── Export ────────────────────────────────────────────────────────────────────
st.divider()
st.subheader("Export to Excel")
if st.button("Generate xlsx"):
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
