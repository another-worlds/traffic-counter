"""
Generate a track-concentration heatmap as an RGBA PNG.

Algorithm:
  1. Build a 2-D histogram of all (cx, cy) track positions at 1/SCALE resolution.
  2. Resize back to the full video dimensions with bilinear interpolation.
  3. Apply a Gaussian blur so the result is smooth rather than pixelated.
  4. Apply a plasma colormap with alpha=0 at zero density so the video frame
     shows through wherever no vehicles have been.

All dependencies (numpy, Pillow) are already in the API image.
"""
from __future__ import annotations

import io
import numpy as np
import pandas as pd
from PIL import Image, ImageFilter


# ── Tuneable constants ────────────────────────────────────────────────────── #
_HIST_SCALE = 4        # work at 1/SCALE resolution for the histogram
_BLUR_DIVISOR = 30     # blur radius = max(width, height) // BLUR_DIVISOR

# Plasma colormap breakpoints: (value 0-255, R, G, B, A)
# value=0 is fully transparent so the video frame shows through.
_STOPS = [
    (0,    0,   0,   0,   0),    # transparent – no tracks
    (10,  13,   8, 135, 100),    # dark indigo
    (80,  190,  26, 158, 180),   # violet
    (160, 237, 105,  37, 210),   # orange
    (255, 240, 249,  33, 230),   # bright yellow
]
# ─────────────────────────────────────────────────────────────────────────── #


def _build_lut() -> np.ndarray:
    """Build a (256, 4) uint8 LUT from the stop table."""
    lut = np.zeros((256, 4), dtype=np.uint8)
    for i in range(len(_STOPS) - 1):
        v0, r0, g0, b0, a0 = _STOPS[i]
        v1, r1, g1, b1, a1 = _STOPS[i + 1]
        for v in range(v0, v1 + 1):
            t = (v - v0) / max(v1 - v0, 1)
            lut[v] = [
                int(r0 + t * (r1 - r0)),
                int(g0 + t * (g1 - g0)),
                int(b0 + t * (b1 - b0)),
                int(a0 + t * (a1 - a0)),
            ]
    return lut


_LUT = _build_lut()


def generate_heatmap(
    tracks_df: pd.DataFrame,
    video_width: int,
    video_height: int,
) -> bytes:
    """
    Return RGBA PNG bytes of the track-density heatmap at (video_width × video_height).

    *tracks_df* must have float columns ``cx`` and ``cy`` in source-pixel space.
    An empty dataframe produces a fully transparent image.
    """
    W, H = max(video_width, 1), max(video_height, 1)

    if tracks_df.empty:
        buf = io.BytesIO()
        Image.new("RGBA", (W, H), (0, 0, 0, 0)).save(buf, format="PNG")
        return buf.getvalue()

    # ── 1. 2-D histogram at reduced resolution ────────────────────────────── #
    w_s = max(W // _HIST_SCALE, 1)
    h_s = max(H // _HIST_SCALE, 1)

    cx = tracks_df["cx"].to_numpy(dtype=np.float32)
    cy = tracks_df["cy"].to_numpy(dtype=np.float32)

    # histogram2d expects (x_vals, y_vals); transpose → (rows=y, cols=x)
    hist, _, _ = np.histogram2d(cx, cy, bins=[w_s, h_s], range=[[0, W], [0, H]])
    hist = hist.T.astype(np.float32)

    # ── 2. Normalise to 0-255 ─────────────────────────────────────────────── #
    hmax = hist.max()
    if hmax > 0:
        # Use a square-root scale so low-density areas are still visible
        hist = np.sqrt(hist / hmax) * 255.0
    hist = hist.clip(0, 255).astype(np.uint8)

    # ── 3. Resize to full resolution + Gaussian blur ──────────────────────── #
    gray = Image.fromarray(hist, mode="L").resize((W, H), Image.BILINEAR)
    radius = max(W, H) // _BLUR_DIVISOR
    gray = gray.filter(ImageFilter.GaussianBlur(radius=max(radius, 1)))

    # ── 4. Apply plasma colormap via LUT ──────────────────────────────────── #
    arr = np.array(gray).ravel()
    rgba = _LUT[arr].reshape(H, W, 4)
    img = Image.fromarray(rgba, mode="RGBA")

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
