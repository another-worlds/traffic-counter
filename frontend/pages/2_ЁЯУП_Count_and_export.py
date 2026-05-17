"""
Count & export page.

Flow:
  1. User picks one or more analyzed videos from the project.
  2. We composite the representative frame + trajectories overlay of the first
     selected video into a single background image, scaled to canvas width.
  3. User draws counting lines on the canvas (drawing_mode='line').
  4. For each line drawn, "Save" stores it on the project; existing saved lines
     are also rendered for context.
  5. "Compute counts" → totals, per-class, per-direction, both percentages.
  6. "Export to Excel" → xlsx download.
"""
import io
import math
import streamlit as st
import httpx
from PIL import Image
from streamlit_drawable_canvas import st_canvas

import api_client as api
from sidebar import render_sidebar

st.set_page_config(page_title="Count & export", page_icon="📏", layout="wide")
st.title("📏 Count & export")

ws = render_sidebar()
if not ws:
    st.warning("Pick a workspace in the sidebar to begin.")
    st.stop()

st.caption(f"Workspace: **{ws['name']}**")

# --- choose videos ---
videos = [v for v in api.list_videos(ws["id"]) if v["status"] == "analyzed"]
if not videos:
    st.info("No analyzed videos in this project yet — analyze one first.")
    st.stop()

video_labels = {f'{v["filename"]} ({v["num_tracks"]} tracks)': v for v in videos}
default_picks = [list(video_labels.keys())[0]]
picks = st.multiselect(
    "Videos to count over",
    options=list(video_labels.keys()),
    default=default_picks,
    help="Counting is computed over the union of all selected videos.",
)
selected = [video_labels[k] for k in picks]
if not selected:
    st.stop()

# --- load background image (frame + trajectories) ---
preview_video = selected[0]

@st.cache_data(show_spinner=False)
def fetch_image(url: str) -> Image.Image:
    r = httpx.get(url, timeout=30.0)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGBA")

with st.spinner("Loading frame and trajectories…"):
    frame_url = api.get_frame_url(preview_video["id"])
    traj_url = api.get_trajectories_url(preview_video["id"])
    if not frame_url or not traj_url:
        st.error("Frame or trajectories overlay missing — re-analyze the video.")
        st.stop()
    bg = fetch_image(api.file_url(frame_url))
    overlay = fetch_image(api.file_url(traj_url))

# Composite: frame underneath, trajectory overlay on top
composite = Image.alpha_composite(bg, overlay.resize(bg.size))

# Scale to fit in the page width
src_w, src_h = composite.size
canvas_w = min(900, src_w)
scale = canvas_w / src_w
canvas_h = int(src_h * scale)
canvas_bg = composite.resize((canvas_w, canvas_h))

st.write(f"Canvas: {canvas_w}×{canvas_h} (source: {src_w}×{src_h}, scale={scale:.3f}×)")

# --- existing saved lines ---
saved_lines = api.list_lines(ws["id"])

# --- canvas: draw new lines ---
col_left, col_right = st.columns([3, 2])

with col_left:
    st.subheader("Draw counting lines")
    st.caption("Click and drag to draw a line. Each line counts vehicle trajectories that cross it.")

    canvas_result = st_canvas(
        fill_color="rgba(0, 0, 0, 0)",
        stroke_width=3,
        stroke_color="#e24b4a",
        background_image=canvas_bg,
        update_streamlit=True,
        height=canvas_h,
        width=canvas_w,
        drawing_mode="line",
        key=f"canvas_{preview_video['id']}",
    )

with col_right:
    st.subheader("Saved lines")
    if not saved_lines:
        st.caption("No saved lines yet.")
    for ln in saved_lines:
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                st.write(f"**{ln['name']}**")
                a, b = ln["points"]["a"], ln["points"]["b"]
                st.caption(f"({a[0]:.0f}, {a[1]:.0f}) → ({b[0]:.0f}, {b[1]:.0f})")
            with c2:
                if st.button("🗑", key=f"del_line_{ln['id']}"):
                    api.delete_line(ln["id"])
                    st.rerun()

# --- extract drawn lines from canvas + save form ---
def line_endpoints_from_fabric(obj: dict):
    """
    Fabric.js Line stored in streamlit-drawable-canvas: the line lies along the
    diagonal of (left, top, width, height). The sign of (x2-x1) and (y2-y1)
    tells us which diagonal — i.e. which corner is endpoint A vs B.
    """
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


drawn = []
if canvas_result.json_data and canvas_result.json_data.get("objects"):
    for obj in canvas_result.json_data["objects"]:
        if obj.get("type") == "line":
            (ax, ay), (bx, by) = line_endpoints_from_fabric(obj)
            # Scale back to source pixel coords
            drawn.append({
                "a_canvas": (ax, ay),
                "b_canvas": (bx, by),
                "a": (ax / scale, ay / scale),
                "b": (bx / scale, by / scale),
            })

if drawn:
    st.subheader("Drawn (unsaved) lines")
    name_default = f"line {len(saved_lines) + 1}"
    for i, d in enumerate(drawn):
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 2, 1])
            with c1:
                st.caption(
                    f"Source coords: ({d['a'][0]:.0f}, {d['a'][1]:.0f}) "
                    f"→ ({d['b'][0]:.0f}, {d['b'][1]:.0f})"
                )
            with c2:
                name = st.text_input(
                    "Name",
                    value=f"line {len(saved_lines) + i + 1}",
                    key=f"name_{i}",
                    label_visibility="collapsed",
                )
            with c3:
                if st.button("Save", key=f"save_{i}"):
                    api.create_line(
                        ws["id"], name,
                        d["a"][0], d["a"][1], d["b"][0], d["b"][1],
                    )
                    st.rerun()

st.divider()

# --- compute counts ---
st.subheader("Counts")
if not saved_lines:
    st.info("Save at least one line to compute counts.")
    st.stop()

include_drawn = st.checkbox(
    "Include unsaved drawn lines (ephemeral)",
    value=bool(drawn),
    help="Adds the lines currently on the canvas to the computation without persisting them.",
)

lines_for_counting = list(saved_lines)
ephemeral_lines = []
if include_drawn:
    # Saving them temporarily would change DB state; instead we count them locally below.
    ephemeral_lines = drawn

if st.button("Compute counts", type="primary"):
    video_ids = [v["id"] for v in selected]
    line_ids = [ln["id"] for ln in lines_for_counting]

    # Server-side counts (saved lines only)
    try:
        result = api.compute_counts(ws["id"], video_ids, line_ids) if line_ids else {
            "total_unique_tracks": 0, "sum_across_lines": 0, "per_line": [],
        }
    except Exception as e:
        st.error(f"Count failed: {e}")
        st.stop()

    # If user asked to include ephemeral lines, save+count+delete (cheapest reliable path)
    if ephemeral_lines:
        ephemeral_ids = []
        for i, d in enumerate(ephemeral_lines):
            ln = api.create_line(
                ws["id"], f"(ephemeral {i+1})",
                d["a"][0], d["a"][1], d["b"][0], d["b"][1],
                color="#888888",
            )
            ephemeral_ids.append(ln["id"])
        try:
            extra = api.compute_counts(ws["id"], video_ids, ephemeral_ids)
            # Splice ephemeral results into the response — recompute pct_of_drawn after merge.
            merged_per_line = list(result["per_line"]) + list(extra["per_line"])
            sum_total = sum(p["total"] for p in merged_per_line)
            T = result["total_unique_tracks"] or extra["total_unique_tracks"]
            for p in merged_per_line:
                p["percent_of_drawn_lines"] = (
                    round(100.0 * p["total"] / sum_total, 2) if sum_total else 0.0
                )
                p["percent_of_video_total"] = (
                    round(100.0 * p["total"] / T, 2) if T else 0.0
                )
            result = {
                "total_unique_tracks": T,
                "sum_across_lines": sum_total,
                "per_line": merged_per_line,
            }
        finally:
            for lid in ephemeral_ids:
                try:
                    api.delete_line(lid)
                except Exception:
                    pass

    # Display
    c1, c2 = st.columns(2)
    c1.metric("Unique tracks in selection", result["total_unique_tracks"])
    c2.metric("Σ counts across lines", result["sum_across_lines"])

    rows = []
    for p in result["per_line"]:
        rows.append({
            "Line": p["line_name"],
            "Count": p["total"],
            "% of video total": p["percent_of_video_total"],
            "% of drawn lines": p["percent_of_drawn_lines"],
            "Dir +": p["by_direction"].get("positive", 0),
            "Dir −": p["by_direction"].get("negative", 0),
            "Car": p["by_class"].get("car", 0),
            "Truck": p["by_class"].get("truck", 0),
            "Bus": p["by_class"].get("bus", 0),
            "Motorcycle": p["by_class"].get("motorcycle", 0),
            "Bicycle": p["by_class"].get("bicycle", 0),
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Export to Excel")
if st.button("Generate xlsx"):
    video_ids = [v["id"] for v in selected]
    line_ids = [ln["id"] for ln in saved_lines]
    if not line_ids:
        st.error("No saved lines to export.")
    else:
        with st.spinner("Building workbook…"):
            data = api.export_xlsx(ws["id"], video_ids, line_ids)
        st.download_button(
            "Download counts.xlsx",
            data=data,
            file_name=f"counts-{ws['name'].replace(' ', '_')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
