# macromodel-api: FastAPI + UXsim macroscopic modelling backend.
# osmnx/geopandas/shapely need a few system libs (GEOS/PROJ are bundled in the wheels,
# but spatialindex for rtree and libgomp for scipy/sklearn-style code are handy).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libspatialindex-dev libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start.sh
EXPOSE 8100
CMD ["./start.sh"]
