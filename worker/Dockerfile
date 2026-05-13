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
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Pre-download the model into /models — a path never covered by any volume mount,
# so the baked file is always used regardless of named-volume state.
RUN mkdir -p /models \
    && python -c "from ultralytics import YOLO; YOLO('yolov8m.pt')" \
    && mv /app/yolov8m.pt /models/yolov8m.pt

# Default to poll mode for local docker-compose; Cloud Run Job overrides with
# WORKER_MODE=single and VIDEO_ID via per-execution env overrides.
CMD ["python", "main.py"]
