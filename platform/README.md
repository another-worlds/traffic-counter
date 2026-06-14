# platform/ — the rebuilt macromodel (clean-slate, target architecture)

This directory is a **ground-up rewrite** of the macromodel subsystem onto the target
architecture from
[`../macromodel/docs/architecture/tooling-landscape.md`](../macromodel/docs/architecture/tooling-landscape.md).
It is **independent** of the legacy `macromodel/` app (Solara + UXsim + GeoJSON-in-JSON) —
nothing here imports or depends on it; they run as separate containers.

> **Why this exists** → [`../macromodel/docs/adr/0002-clean-slate-rewrite-on-target-architecture.md`](../macromodel/docs/adr/0002-clean-slate-rewrite-on-target-architecture.md)
> · **What's done / next** → [`ROADMAP.md`](ROADMAP.md)
> · **The contract** → [`docs/data-contract.md`](docs/data-contract.md)

## Services

| Service | What it is | Port | Status |
|---|---|---|---|
| `model-db` | PostGIS 16 / 3.4 — the single source of truth, **native geometry + GiST** | `5434:5432` | ✅ this commit |
| `core-api` (`platform/core-api`) | FastAPI + SQLAlchemy 2 + **GeoAlchemy2** — the data-contract foundation | `8200:8200` | ✅ this commit |
| *(later)* tiles | Martin — PostGIS → MVT | — | planned |
| *(later)* web | React + MapLibre + deck.gl + Dockview shell | — | planned |
| *(later)* worker | Procrastinate worker (AequilibraE / ODME runs) | — | planned |

## What's in this first commit — the data-contract foundation

The foundation everything else depends on: a **native-PostGIS** schema (not GeoJSON-in-a-
JSON-column), a **typed data contract** (GeoJSON at the API edge), and proof that the
load-bearing spatial capabilities work.

- `app/models.py` — the canonical schema with **native `geometry` columns** (POINT/LINESTRING/
  POLYGON, SRID 4326) and auto-created **GiST** indexes. Field names track **GMNS**; OD matrices
  reference out-of-row **OMX** artefacts.
- `app/schemas.py` — the Pydantic v2 request/response contract (geometry as GeoJSON).
- `app/geo.py` — the GeoJSON ⇄ native-geometry boundary + geodesic lengths.
- `app/routers/` — scenario CRUD, network CRUD, the GeoJSON `/network` export, and the
  **KNN `/snap`** endpoint (the basis of the counts→link bridge).
- `tests/` — prove geometry round-trips losslessly, lengths are geodesic, and **GiST-assisted
  KNN** picks the right link.

See [`docs/data-contract.md`](docs/data-contract.md) for the full contract.

## Run it

```bash
# from the repo root
docker compose up -d --build model-db core-api
curl localhost:8200/healthz
# OpenAPI docs: http://localhost:8200/docs

# run the contract tests (inside the container, against model-db)
docker compose exec -T core-api pytest -q tests
```

## Why a rewrite, not a migration

The legacy macromodel stores geometry as GeoJSON inside JSON columns, which **cannot be
GiST-indexed** — so KNN snapping, `ST_DWithin`, and in-DB `ST_AsMVT` tiling are impossible
without first re-laying the foundation. Rather than retrofit, this rewrite starts from native
geometry so the rest of the target stack (Martin tiles, AequilibraE materialisation, the
topology edit service) sits on solid ground. The legacy app keeps running untouched until the
rebuild reaches feature parity.
