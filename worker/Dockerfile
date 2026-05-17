# Cloud Run L4 ships with NVIDIA driver 535.x (CUDA 12.2 compatible).
# Match that with a CUDA 12.x runtime base.
FROM nvidia/cuda:12.2.2-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        ffmpeg libgl1 libglib2.0-0 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

WORKDIR /app
COPY requirements.txt .
# ultralytics depends on opencv-python (GUI variant) by name; the system libs
# libgl1 and libglib2.0-0 are already installed above so cv2 imports fine on
# a headless server.  No swap needed — clean cache, simple build.
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Download model weights at build time directly into /models/.
# Ultralytics downloads to the parent directory of the given path, so
# YOLO('/models/yolov8m.pt') fetches straight to /models/ — no mv needed.
# .dockerignore excludes *.pt so no stale local file can bypass this step.
RUN mkdir -p /models \
    && python -c "from ultralytics import YOLO; YOLO('/models/yolov8m.pt')"

# Default to poll mode for local docker-compose; Cloud Run Job overrides with
# WORKER_MODE=single and VIDEO_ID via per-execution env overrides.
CMD ["python", "main.py"]
