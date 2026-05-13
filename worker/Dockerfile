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
# ultralytics pulls in opencv-python (full GUI build) as a transitive dep,
# which overwrites our headless install because they share the cv2 namespace.
# Correct sequence: install everything, evict the GUI build, reinstall headless.
# OPENCV_FIX is a build-arg bump to invalidate the Docker cache for this layer
# whenever the fix logic changes (e.g. `docker compose build --build-arg OPENCV_FIX=2`).
ARG OPENCV_FIX=2
RUN pip install --no-cache-dir -r requirements.txt \
 && pip uninstall -y opencv-python 2>/dev/null || true \
 && pip install --no-cache-dir opencv-python-headless==4.10.0.84

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
