"""Overlay numbered callouts on a screenshot of the Count & Export page.

Reads ``docs/usage-guide.source.png`` (a raw screenshot, committed alongside
this script) and writes ``docs/usage-guide.png`` with arrows + numbered
circles pointing at each region of the UI. Re-run after a UI change.

Anchor coordinates are fractional (0..1) so the script tolerates any
screenshot resolution.
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


# --- Callout positions (fractional coords on the source image) -------------
#
# (anchor_x, anchor_y) is where the arrow tip lands on the UI.
# (label_x,  label_y)  is where the numbered chip + caption sit.
# The script draws a curved arrow from the chip to the anchor.

@dataclass
class Callout:
    n: int
    anchor: tuple[float, float]
    label_pos: tuple[float, float]
    text: str


CALLOUTS: list[Callout] = [
    Callout(1,  (0.34, 0.45), (0.02, 0.30), "Click-drag empty space → new counting line"),
    Callout(2,  (0.345, 0.22), (0.02, 0.10), "Drag the white handles to fine-tune"),
    Callout(3,  (0.74, 0.03),  (0.55, 0.005), "Frame / scene indicator"),
    Callout(4,  (0.82, 0.11),  (0.66, 0.005), "Live total across selected lines"),
    Callout(5,  (0.71, 0.235), (0.66, 0.30),  "Toggle overlays"),
    Callout(6,  (0.68, 0.33),  (0.66, 0.39),  "Color used for the next line you draw"),
    Callout(7,  (0.82, 0.45),  (0.66, 0.50),  "Rename, recolor, see counts, or delete"),
    Callout(8,  (0.71, 0.555), (0.66, 0.61),  "Pick N — get suggested lines from the API"),
    Callout(9,  (0.71, 0.645), (0.66, 0.69),  "Save / load lines as JSON"),
    Callout(10, (0.71, 0.815), (0.66, 0.78),  "Inbound vs outbound split"),
    Callout(11, (0.88, 0.97),  (0.66, 0.93),  "Per-class crossings across selection"),
]


# --- Drawing primitives ----------------------------------------------------

RED = (255, 75, 75)
WHITE = (255, 255, 255)
BLACK_PILL = (10, 12, 18, 220)


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _draw_numbered_chip(
    layer: ImageDraw.ImageDraw, x: int, y: int, n: int, font: ImageFont.ImageFont
) -> None:
    r = 14
    layer.ellipse((x - r - 2, y - r - 2, x + r + 2, y + r + 2), fill=WHITE)
    layer.ellipse((x - r, y - r, x + r, y + r), fill=RED)
    text = str(n)
    bbox = layer.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    layer.text((x - w / 2, y - h / 2 - bbox[1]), text, font=font, fill=WHITE)


def _draw_label_pill(
    layer: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font: ImageFont.ImageFont,
) -> tuple[int, int, int, int]:
    pad_x, pad_y = 10, 6
    bbox = layer.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0] + pad_x * 2
    h = bbox[3] - bbox[1] + pad_y * 2
    rect = (x, y, x + w, y + h)
    layer.rounded_rectangle(rect, radius=8, fill=BLACK_PILL, outline=RED, width=1)
    layer.text((x + pad_x, y + pad_y - bbox[1]), text, font=font, fill=WHITE)
    return rect


def _draw_arrow(
    layer: ImageDraw.ImageDraw,
    start: tuple[int, int],
    end: tuple[int, int],
) -> None:
    sx, sy = start
    ex, ey = end
    mx, my = (sx + ex) / 2, (sy + ey) / 2
    dx, dy = ex - sx, ey - sy
    # Perpendicular nudge to bow the arrow outward.
    length = max(1.0, math.hypot(dx, dy))
    nx, ny = -dy / length, dx / length
    bow = min(60.0, length * 0.18)
    cx, cy = mx + nx * bow, my + ny * bow

    # Polyline approximation of a quadratic Bézier (simple and font-free).
    steps = 28
    prev = (sx, sy)
    for i in range(1, steps + 1):
        t = i / steps
        bx = (1 - t) ** 2 * sx + 2 * (1 - t) * t * cx + t ** 2 * ex
        by = (1 - t) ** 2 * sy + 2 * (1 - t) * t * cy + t ** 2 * ey
        layer.line([prev, (bx, by)], fill=WHITE, width=3)
        prev = (bx, by)

    # Arrowhead at the end, oriented along the final tangent.
    tx = 2 * (1 - 1.0) * (cx - sx) + 2 * 1.0 * (ex - cx)
    ty = 2 * (1 - 1.0) * (cy - sy) + 2 * 1.0 * (ey - cy)
    ang = math.atan2(ty, tx)
    head = 12
    p1 = (ex - head * math.cos(ang - math.pi / 7),
          ey - head * math.sin(ang - math.pi / 7))
    p2 = (ex - head * math.cos(ang + math.pi / 7),
          ey - head * math.sin(ang + math.pi / 7))
    layer.polygon([(ex, ey), p1, p2], fill=WHITE)


# --- Pipeline --------------------------------------------------------------

def render(input_path: Path, output_path: Path) -> None:
    base = Image.open(input_path).convert("RGBA")
    W, H = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    layer = ImageDraw.Draw(overlay)

    chip_font = _load_font(13)
    label_font = _load_font(13)

    # First pass: shadows behind every arrow so they read on bright frames.
    for c in CALLOUTS:
        ax, ay = int(c.anchor[0] * W), int(c.anchor[1] * H)
        lx, ly = int(c.label_pos[0] * W), int(c.label_pos[1] * H)
        # Start arrow from the chip's edge, not its centre, by nudging
        # 18 px toward the anchor.
        dx, dy = ax - lx, ay - ly
        d = max(1.0, math.hypot(dx, dy))
        start = (lx + int(18 * dx / d), ly + int(18 * dy / d))
        end_pull = 22
        end = (ax - int(end_pull * dx / d), ay - int(end_pull * dy / d))
        _draw_arrow(shadow_draw, start, end)

    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=3))
    base.alpha_composite(shadow_layer)

    # Second pass: actual arrows + chips + labels.
    for c in CALLOUTS:
        ax, ay = int(c.anchor[0] * W), int(c.anchor[1] * H)
        lx, ly = int(c.label_pos[0] * W), int(c.label_pos[1] * H)
        rect = _draw_label_pill(layer, lx, ly, f"{c.n}. {c.text}", label_font)
        # Arrow starts at the right-edge midpoint of the label pill, ends
        # at the anchor (with a small inset).
        chip_cx = rect[0] - 18
        chip_cy = (rect[1] + rect[3]) // 2
        # Numbered chip sits just left of the pill.
        _draw_numbered_chip(layer, chip_cx, chip_cy, c.n, chip_font)
        # Arrow from pill to anchor.
        dx, dy = ax - rect[2], ay - chip_cy
        d = max(1.0, math.hypot(dx, dy))
        start = (rect[2] + 4, chip_cy)
        end = (ax - int(22 * dx / d), ay - int(22 * dy / d))
        _draw_arrow(layer, start, end)
        # Solid anchor dot.
        layer.ellipse((ax - 5, ay - 5, ax + 5, ay + 5), fill=RED, outline=WHITE, width=2)

    base.alpha_composite(overlay)
    base.convert("RGB").save(output_path, format="PNG", optimize=True)


def main() -> None:
    here = Path(__file__).parent
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--input", type=Path, default=here / "usage-guide.source.png")
    p.add_argument("--output", type=Path, default=here / "usage-guide.png")
    args = p.parse_args()
    if not args.input.exists():
        raise SystemExit(
            f"Source screenshot not found: {args.input}\n"
            "Save the Count & Export screenshot to that path, then re-run.",
        )
    render(args.input, args.output)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
