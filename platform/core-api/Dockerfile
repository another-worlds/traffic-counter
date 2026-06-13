FROM python:3.11-slim

WORKDIR /app

# curl is only for the container healthcheck; shapely/pyproj/psycopg ship
# manylinux wheels that bundle GEOS/PROJ/libpq, so no system geo libs needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8200

# init_db (PostGIS extension + create_all + GiST) runs in the FastAPI lifespan.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8200"]
