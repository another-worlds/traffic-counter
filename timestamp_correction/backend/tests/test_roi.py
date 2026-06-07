"""Verify timestamp processing uses ROI crops only, not full frames."""
from __future__ import annotations

import numpy as np
import pytest

from worker.region_locator import Region
from worker.roi import crop_region, region_area_fraction, validate_region_size


def test_crop_region_extracts_subrectangle():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[40:70, 500:620] = 255
    region = Region(x=500, y=40, w=120, h=30, confidence=0.9, method="test")
    crop = crop_region(frame, region)
    assert crop.shape == (30, 120, 3)
    assert crop.mean() == 255.0


def test_crop_region_clamps_to_frame_bounds():
    frame = np.ones((100, 200, 3), dtype=np.uint8)
    region = Region(x=180, y=90, w=50, h=50, confidence=0.5, method="test")
    crop = crop_region(frame, region)
    assert crop.shape == (10, 20, 3)


def test_validate_region_size_rejects_oversized_bbox():
    region = Region(x=0, y=0, w=400, h=200, confidence=0.5, method="test")
    assert region_area_fraction(region, 640, 480) > 0.12
    with pytest.raises(ValueError, match="false positive"):
        validate_region_size(region, 640, 480)


def test_sample_timeline_passes_crops_not_full_frames(monkeypatch):
    """OCR must receive small ROI arrays, never full decoded frames."""
    from worker import timestamp_reader

    region = Region(x=10, y=10, w=80, h=24, confidence=1.0, method="test")
    frame_w, frame_h = 640, 480
    received_shapes: list[tuple[int, ...]] = []

    def fake_read(bgr: np.ndarray):
        received_shapes.append(bgr.shape)
        return 1_700_000_000.0, "2023-11-11 12:00:00"

    monkeypatch.setattr(timestamp_reader, "read_timestamp_from_crop", fake_read)

    class FakeCap:
        def __init__(self):
            self._pos = 0

        def set(self, prop, value):
            if prop == 0:  # CAP_PROP_POS_FRAMES
                self._pos = int(value)

        def grab(self):
            if self._pos >= 30:
                return False
            self._pos += 1
            return True

        def read(self):
            if self._pos >= 30:
                return False, None
            frame = np.zeros((frame_h, frame_w, 3), dtype=np.uint8)
            self._pos += 1
            return True, frame

    samples = timestamp_reader.sample_timeline(
        FakeCap(), region, fps=25.0, sample_interval_s=1.0, total_frames=30,
    )
    assert len(samples) >= 1
    for shape in received_shapes:
        assert shape[0] == region.h
        assert shape[1] == region.w
        assert shape != (frame_h, frame_w, 3)