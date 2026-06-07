"""Video decode helpers — ffmpeg log suppression and resilient frame reads."""
from __future__ import annotations

import os


def configure_quiet_decode() -> None:
    """Reduce HEVC reference-frame noise on stderr during OpenCV reads."""
    os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "quiet")
    os.environ.setdefault("AV_LOG_LEVEL", "quiet")