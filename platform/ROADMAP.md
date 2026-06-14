# platform/ — roadmap & status

> Living state of the macromodel rewrite. **Why** decisions were made → `macromodel/docs/adr/`.
> **Target** architecture → `macromodel/docs/architecture/tooling-landscape.md`.
> **Contract** → `platform/docs/data-contract.md`. Updated: 2026-06-14.

## Done

- **Data-contract foundation** — `platform/core-api` (commit `33de394`).
  - Native PostGIS geometry (GeoAlchemy2, SRID 4326) + **GiST** indexes — the break from the
    legacy GeoJSON-in-JSON. Typed GeoJSON contract; scenario + network CRUD; GeoJSON `/network`
    export; **GiST-assisted KNN `/snap`**. GMNS-aligned fields; matrices reference out-of-row OMX.
  - New compose services: `model-db` (PostGIS, :5434), `core-api` (:8200).
  - **Verified vs real PostGIS:** 4/4 contract tests (geometry round-trip, geodesic length,
    `/network` FeatureCollection, KNN snap, GiST index present) + containerized smoke
    (`/healthz` + create→snap). Image builds (in-container `pip` needs the proxy CA in the web sandbox).

## Next (prioritized)

1. **Topology edit service** — split/merge link, snap/move node, with GMNS-consistent
   re-noding and length recompute. The largest "build" gap in the north-star report.
2. **Engine materialisation** — PostGIS → GMNS/OMX → AequilibraE assignment; write per-link
   volumes back as `AssignmentResult` (OMX, out-of-row).
3. **Seeds / demo network** — default classes (link/node/zone types, modes, activities, demand
   layers) + a small seeded scenario, so the API/UI has something to render.
4. **Counter→link bridge** — ingest `traffic-counter` observations and snap them to links
   (builds directly on `/snap`), populating `counters.observed_vph`.
5. **ODME / calibration** — select-link-incidence ODME against counters (`CalibrationRun`).
6. **Tiles + web shell** — Martin (PostGIS→MVT) + React/MapLibre/deck.gl + Dockview (later segments).

## Deferred / open decisions

- **Async SQLAlchemy** under psycopg3 — the report flags GeoAlchemy2 async as under-documented;
  the foundation deliberately uses proven **sync** sessions. Spike before adopting.
- **Alembic** migrations — greenfield uses `create_all`; adopt Alembic before schema churn.
- Demand/procedure tables are defined in `models.py` (part of the canonical contract) but have
  no routers/engines yet — they land with items 2–5 above.
