# syntax=docker/dockerfile:1
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
# BuildKit cache mount persists pip's wheel cache across rebuilds.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt
COPY main.py video_exts.py ./
CMD ["python", "main.py"]
