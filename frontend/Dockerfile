FROM node:22-slim AS vite-builder
WORKDIR /build
COPY hybrid_viewport/package.json hybrid_viewport/package-lock.json ./
RUN npm ci
COPY hybrid_viewport/ ./
RUN npm run build

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Place built assets OUTSIDE /app so the ./frontend:/app bind-mount in docker-compose
# does not shadow them at runtime.  The bridge reads HYBRID_VIEWPORT_DIST_PATH.
COPY --from=vite-builder /build/dist /srv/hybrid_viewport_dist

ENV PYTHONUNBUFFERED=1 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    HYBRID_VIEWPORT_DIST_PATH=/srv/hybrid_viewport_dist

EXPOSE 8501
CMD ["streamlit", "run", "streamlit_app.py"]
