# CUDA runtime for EasyOCR + RegionLocatorNet GPU inference.
FROM nvidia/cuda:12.2.2-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        libgl1 libglib2.0-0 libgomp1 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && ln -sf /usr/bin/pip3 /usr/local/bin/pip

WORKDIR /app
COPY requirements.txt .
# CUDA-enabled PyTorch first; remaining deps reuse this wheel.
RUN pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121 \
    && pip install -r requirements.txt
# Bake EasyOCR weights (CPU init at build — GPU enabled at runtime when available).
RUN python -c "import easyocr; easyocr.Reader(['en'], gpu=False, verbose=False)"

COPY . .
RUN chmod +x start.sh start_worker.sh
EXPOSE 8200
CMD ["./start.sh"]