# syntax=docker/dockerfile:1
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# BuildKit cache mount persists pip's wheel cache across rebuilds so an
# invalidated install layer reuses downloaded wheels instead of re-fetching.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY app ./app
COPY start.sh .

RUN chmod +x start.sh

ENV PYTHONUNBUFFERED=1 \
    PORT=8000

CMD ["./start.sh"]
