"""Timeline OCR sampling — seek mode decodes only sample frames."""
from __future__ import annotations

import numpy as np

from worker.region_locator import Region
from worker import timestamp_reader
from worker.timestamp_reader import compute_sample_stride_frames


def test_stride_scales_with_fps():
    assert compute_sample_stride_frames(25.0, 300.0) == 7500
    assert compute_sample_stride_frames(30.0, 300.0) == 9000
    assert compute_sample_stride_frames(50.0, 300.0) == 15000


def test_sample_stride_seek_mode(monkeypatch):
    region = Region(x=0, y=0, w=40, h=20, confidence=1.0, method="test")
    fps = 25.0
    interval_s = 60.0
    interval_frames = compute_sample_stride_frames(fps, interval_s)
    total_frames = interval_frames * 3 + 1

    monkeypatch.setattr(
        timestamp_reader,
        "read_timestamp_from_crop",
        lambda _crop: (1_700_000_000.0, "2024-01-01 12:00:00"),
    )

    class FakeCap:
        def __init__(self):
            self.grab_calls = 0
            self.read_calls = 0
            self._pos = 0

        def set(self, prop, value):
            if prop == 0:  # CAP_PROP_POS_FRAMES
                self._pos = int(value)

        def get(self, prop):
            if prop == 0:
                return float(self._pos)
            return 0.0

        def read(self):
            self.read_calls += 1
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            return True, frame

        def grab(self):
            self.grab_calls += 1
            return True

    cap = FakeCap()
    samples = timestamp_reader.sample_timeline(
        cap,
        region,
        fps=fps,
        sample_interval_s=interval_s,
        total_frames=total_frames,
        sample_mode="seek",
    )

    assert len(samples) == 4
    assert [s.frame_idx for s in samples] == [0, interval_frames, interval_frames * 2, interval_frames * 3]
    assert cap.read_calls == 4
    assert cap.grab_calls == 0


def test_sample_stride_sequential_mode(monkeypatch):
    region = Region(x=0, y=0, w=40, h=20, confidence=1.0, method="test")
    fps = 25.0
    interval_s = 300.0
    interval_frames = compute_sample_stride_frames(fps, interval_s)
    total_frames = interval_frames * 3 + 1

    monkeypatch.setattr(
        timestamp_reader,
        "read_timestamp_from_crop",
        lambda _crop: (1_700_000_000.0, "2024-01-01 12:00:00"),
    )

    class FakeCap:
        def __init__(self):
            self._pos = 0
            self.grab_calls = 0
            self.read_calls = 0

        def grab(self):
            self.grab_calls += 1
            if self._pos >= total_frames:
                return False
            self._pos += 1
            return True

        def read(self):
            self.read_calls += 1
            if self._pos >= total_frames:
                return False, None
            frame = np.zeros((480, 640, 3), dtype=np.uint8)
            self._pos += 1
            return True, frame

    cap = FakeCap()
    samples = timestamp_reader.sample_timeline(
        cap,
        region,
        fps=fps,
        sample_interval_s=interval_s,
        total_frames=total_frames,
        sample_mode="sequential",
    )

    assert len(samples) == 4
    assert cap.read_calls == 4
    assert cap.grab_calls == total_frames - 4