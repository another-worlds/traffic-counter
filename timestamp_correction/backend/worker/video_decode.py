"""Video decode helpers — ffmpeg log suppression and resilient frame reads."""
from __future__ import annotations

import os
from typing import Tuple

import cv2


def configure_quiet_decode() -> None:
    """Reduce HEVC reference-frame noise on stderr during OpenCV reads."""
    os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "quiet")
    os.environ.setdefault("AV_LOG_LEVEL", "quiet")


def seek_to_frame(cap, frame_idx: int, fps: float) -> int:
    """Seek near ``frame_idx``. Returns actual frame index reported by the capture."""
    frame_idx = max(0, int(frame_idx))
    if fps > 1.0:
        cap.set(cv2.CAP_PROP_POS_MSEC, (frame_idx / fps) * 1000.0)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    return int(cap.get(cv2.CAP_PROP_POS_FRAMES) or frame_idx)


def read_frame_at(cap, frame_idx: int, fps: float) -> Tuple[bool, object]:
    """Seek and decode a single frame."""
    seek_to_frame(cap, frame_idx, fps)
    return cap.read()