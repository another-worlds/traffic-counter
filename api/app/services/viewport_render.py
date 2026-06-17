"""Render viewport-style snapshots for XLSX export (frame + line + counts)."""
from __future__ import annotations

import io
import logging
import math
from typing import Dict, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from .storage import get_storage, key_frame, key_scene_frame

log = logging.getLogger("api.viewport_render")

EXCEL_MAX_WIDTH = 520


def _hex_to_rgb(color: str) -> Tuple[int, int, int]:
    raw = (color or "#e24b4a").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return (226, 75, 74)
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))


def _load_font(size: int, *, bold: bool = True) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = (
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf",
    )
    roots = (
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/liberation",
    )
    for root in roots:
        for name in names:
            path = f"{root}/{name}"
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def load_preview_frame_bytes(project_id: str, video_id: str) -> Optional[bytes]:
    storage = get_storage()
    for key in (
        key_frame(project_id, video_id),
        key_scene_frame(project_id, video_id, 0),
    ):
        if storage.exists(key):
            with storage.open_read(key) as fp:
                return fp.read()
    return None


def _draw_label(
    draw: ImageDraw.ImageDraw,
    xy: Tuple[float, float],
    text: str,
    color: Tuple[int, int, int],
    font: ImageFont.ImageFont,
) -> None:
    x, y = xy
    draw.text((x + 1, y + 1), text, fill=(0, 0, 0), font=font)
    draw.text((x, y), text, fill=color, font=font)


def _arrow_head(
    tip: Tuple[float, float],
    direction: Tuple[float, float],
    size: float = 6,
) -> list[Tuple[float, float]]:
    tx, ty = tip
    dx, dy = direction
    base_x = tx - dx * size * 1.4
    base_y = ty - dy * size * 1.4
    px, py = -dy, dx
    return [
        (tx, ty),
        (base_x + px * size, base_y + py * size),
        (base_x - px * size, base_y - py * size),
    ]


def render_line_viewport_snapshot(
    frame_bytes: bytes,
    *,
    line: Dict,
    line_counts: Dict,
    video_width: int,
    video_height: int,
    direction: str,
    max_width: int = EXCEL_MAX_WIDTH,
) -> bytes:
    """PNG bytes mimicking the React viewport for one line and direction block."""
    base = Image.open(io.BytesIO(frame_bytes)).convert("RGBA")
    src_w, src_h = base.size
    if not video_width or not video_height:
        video_width, video_height = src_w, src_h

    scale = min(1.0, max_width / max(src_w, 1))
    if scale < 1.0:
        base = base.resize(
            (max(1, int(src_w * scale)), max(1, int(src_h * scale))),
            Image.Resampling.LANCZOS,
        )
    draw = ImageDraw.Draw(base)
    w, h = base.size
    coord_scale = w / max(video_width, 1)

    color = _hex_to_rgb(line.get("color") or "#e24b4a")
    a = line.get("a") or [0, 0]
    b = line.get("b") or [0, 0]
    ax, ay = float(a[0]) * coord_scale, float(a[1]) * coord_scale
    bx, by = float(b[0]) * coord_scale, float(b[1]) * coord_scale
    stroke = max(3, round(6 * coord_scale))
    draw.line([(ax, ay), (bx, by)], fill=color + (255,), width=stroke)

    mid_x = (ax + bx) / 2
    mid_y = (ay + by) / 2
    total = int(line_counts.get("total") or 0)
    label = f"{line.get('name') or 'line'} · {total}"
    label_font = _load_font(max(14, round(24 * coord_scale)))
    label_offset = max(10, round(19 * coord_scale))
    bbox = draw.textbbox((0, 0), label, font=label_font)
    label_w = bbox[2] - bbox[0]
    _draw_label(draw, (mid_x - label_w / 2, mid_y - label_offset), label, color, label_font)

    bdir = line_counts.get("by_direction") or {}
    pos = int(bdir.get("positive") or 0)
    neg = int(bdir.get("negative") or 0)

    dx = bx - ax
    dy = by - ay
    length = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / length, dx / length

    arrow_gap = max(6, round(10 * coord_scale))
    arrow_len = max(10, round(16 * coord_scale))
    text_gap = arrow_gap + arrow_len + max(10, round(12 * coord_scale))
    side_font = _load_font(max(11, round(17 * coord_scale)))

    direction_label = "Прямое" if direction == "positive" else "Обратное"
    caption_font = _load_font(max(10, round(13 * coord_scale)), bold=False)
    _draw_label(draw, (8, 8), direction_label, (255, 255, 255), caption_font)

    def _render_side(side: int, count: int, *, active: bool) -> None:
        if count <= 0 and not active:
            return
        dir_x = side * nx
        dir_y = side * ny
        start_x = mid_x + dir_x * arrow_gap
        start_y = mid_y + dir_y * arrow_gap
        tip_x = mid_x + dir_x * (arrow_gap + arrow_len)
        tip_y = mid_y + dir_y * (arrow_gap + arrow_len)
        text_x = mid_x + dir_x * text_gap
        text_y = mid_y + dir_y * text_gap
        arrow_color = color if active else tuple(max(0, c - 80) for c in color)
        width = stroke if active else max(2, stroke - 1)
        draw.line(
            [(start_x, start_y), (tip_x, tip_y)],
            fill=arrow_color + (255,),
            width=width,
        )
        draw.polygon(_arrow_head((tip_x, tip_y), (dir_x, dir_y), size=max(4, 5 * coord_scale)), fill=arrow_color)
        text = str(count)
        tb = draw.textbbox((0, 0), text, font=side_font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        # Rotate text by drawing on micro canvas — PIL lacks easy rotate on draw.
        # Use anchor with offset; for small angles we draw horizontal near the side.
        fill = color if active else (200, 200, 200)
        _draw_label(draw, (text_x - tw / 2, text_y - th / 2), text, fill, side_font)

    active_pos = direction == "positive"
    active_neg = direction == "negative"
    _render_side(+1, pos, active=active_pos)
    _render_side(-1, neg, active=active_neg)

    out = io.BytesIO()
    base.convert("RGB").save(out, format="PNG", optimize=True)
    return out.getvalue()