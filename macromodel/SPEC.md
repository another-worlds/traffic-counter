# MacroModel — Specification

> An open, Dockerised **macroscopic traffic-modelling** application — a lightweight
> spiritual cousin of PTV Visum that turns the directional vehicle counts produced by
> the **traffic-counter** app into a calibrated 4-step transport model.

**Status:** MVP · **Codename:** `macromodel` · **Companion to:** `traffic-counter`

---

## 1. Why this exists

`traffic-counter` produces excellent raw data — directional vehicle counts per counting
line, broken down by class (bicycle / car / motorcycle / bus / truck) — but everything is
expressed in **video-pixel space**: a counting line is a pair of pixel coordinates and its
direction is "positive / negative" relative to the image axes. There is no geography, no
road network, and no notion of travel demand.

A transport planner needs the next stages: place those counts on a **real road network**,
build a **demand model**, simulate **network flows**, and **calibrate** the model so it
reproduces what was actually measured. Commercial tools (PTV Visum, EMME, Aimsun) do this
but are closed and expensive. `macromodel` packages the whole pipeline into one Dockerised
app built from open components.

## 2. Scope

**In scope (MVP)**

1. **DB-backed API** — a FastAPI service with its own (PostGIS-capable) database.
2. **Interactive map** — an **ipyleaflet** UI served by **Solara**.
3. **Macroscopic simulation** — **UXsim** as the network assignment engine.
4. **Georeferencing** — pin traffic-counter counting lines onto network links and convert
   pixel-space, image-relative counts into **directional hourly link volumes (vph / PCU)**.
5. **4-step model + calibration** — trip generation → distribution → mode choice → assignment,
   then **ODME calibration** against the observed counts, scored with the **GEH** statistic.

**Out of scope (roadmap):** multinomial-logit mode choice, multi-class assignment, time-dynamic
OD profiles, transit, native PostGIS geometry columns + Alembic migrations, authentication,
matrix import/export to Visum/EMME, scenario versioning/diffing.

## 3. Architecture

```
┌────────────────────┐   REST (counts)    ┌─────────────────────────┐   SQLAlchemy   ┌───────────────┐
│ traffic-counter API│◀───────────────────│  macromodel-api  :8100  │◀──────────────▶│ macromodel-db │
│ (existing  :8000)  │   httpx client     │  FastAPI + UXsim        │                │ postgis :5433 │
└────────────────────┘                    │  4-step + ODME + georef │                └───────────────┘
                                           └─────────────────────────┘
┌────────────────────┐   REST (model)              ▲
│ macromodel-ui :8866│─────────────────────────────┘     artifacts (OD matrices, link flows as parquet)
│ Solara + ipyleaflet│                                    → named volume  macromodel_storage:/data
└────────────────────┘
```

Three new containers are added to the repo's `docker-compose.yml`. The model DB is **separate**
from the counter's DB so we never touch the counter's schema; the only coupling is the
documented REST API.

| Service | Image / build | Port | Role |
|---|---|---|---|
| `macromodel-db` | `postgis/postgis:16-3.4` | 5433→5432 | Model database (spatial-ready) |
| `macromodel-api` | `./macromodel/backend` | 8100 | API, UXsim engine, 4-step, calibration, counter client |
| `macromodel-ui` | `./macromodel/frontend` | 8866 | Solara + ipyleaflet map app |

> **MVP storage note:** the DB image is PostGIS so spatial features are available, but the MVP
> ORM stores geometry as **GeoJSON in JSON columns** and does spatial work (nearest-link snapping,
> skims) in **shapely / networkx / pyproj**. Native PostGIS geometry columns + GiST indexes via
> GeoAlchemy2 are the documented upgrade path.

## 4. Data model (`macromodel-db`)

| Table | Purpose / key columns |
|---|---|
| `scenarios` | top-level container — `id, name, description, bbox, created_at` |
| `nodes` | network nodes — `id, scenario_id, name, geom(Point GeoJSON), osm_id` |
| `links` | **directed** links — `id, scenario_id, from_node_id, to_node_id, geom(LineString), length_m, lanes, free_flow_speed_ms, jam_density, capacity_vph, oneway, osm_id` |
| `zones` | TAZ — `id, scenario_id, name, geom(Polygon), centroid, connector_node_id, production, attraction` |
| `counters` | georeferenced counters — `id, scenario_id, name, geom(Point), snapped_link_id, link_direction(AB/BA), source_video_id, source_line_id, observed_vph, pcu_vph, hours` |
| `observations` | raw pulled counts — `id, counter_id, link_id, vehicle_class, direction, count_total, hours, vph` |
| `od_matrices` | OD artifacts — `id, scenario_id, name, step(seed/distributed/calibrated), n_zones, storage_ref, created_at` |
| `assignment_results` | sim outputs — `id, scenario_id, od_matrix_id, storage_ref, mean_geh, rmse, total_vkt, created_at` |
| `calibration_runs` | `id, scenario_id, method, iterations, od_matrix_id, mean_geh, pct_geh_lt5, converged, created_at` |

OD matrices and per-link flow vectors are written as **parquet in `macromodel_storage`** (keyed by
id); rows hold only metadata + a `storage_ref`.

## 5. The georeferencing bridge (counts → link volume)

This is the conceptual heart of the integration. A traffic-counter counting line gives totals
*by class* and *by direction* (positive/negative) over the whole video. We turn that into a
directional hourly link volume:

1. The user places a counter at a `(lat, lon)` and selects its source counting line
   (`source_video_id`, `source_line_id`).
2. `georef.snap_to_link` finds the nearest **directed** link (shapely STRtree); the user toggles
   whether the line's *positive* direction corresponds to the link's `A→B` (`AB`) or `B→A` (`BA`).
3. We pull counts (`POST /videos/{id}/counts`) and the video `duration_s` (`GET /videos/{id}`),
   then compute **`hours = duration_s / 3600`** and:
   - `observed_vph = by_direction[chosen] / hours`
   - `pcu_vph = observed_vph × pcu_factor`, where `pcu_factor` is the class-mix-weighted average
     of the **PCU table** below (the model is car-equivalent / single-class for the MVP).

| class | bicycle | car | motorcycle | bus | truck |
|---|---|---|---|---|---|
| **PCU** | 0.3 | 1.0 | 0.5 | 2.5 | 2.0 |

These `pcu_vph` values are the **calibration targets** on the corresponding directed links.

## 6. The macroscopic pipeline

### 6.1 Network → UXsim (`netconvert.py`, req 3)
- Source: **OSMnx** `graph_from_bbox` (drive network) **or** a bundled sample grid **or** uploaded GeoJSON.
- Project lat/lon → local **UTM** (pyproj) for node `x,y` and link lengths.
- `free_flow_speed` from OSM `maxspeed`/road-class default; `number_of_lanes` from OSM; `jam_density ≈ 0.2 veh/m`.
- Build a UXsim `World`, `addNode`/`addLink` per row; keep `link.id ↔ UXsim link name` so results join back.
- A parallel **networkx** `DiGraph` (edge weight = free-flow travel time) provides skims and the ODME path↔link incidence.

### 6.2 Four-step model (req 5)
1. **Trip generation** — productions `Pᵢ` / attractions `Aⱼ` per zone (user-set or area-proportional, balanced so `ΣP = ΣA`).
2. **Trip distribution** — doubly-constrained **gravity** model with Furness/IPF balancing:
   `Tᵢⱼ = aᵢ Pᵢ · bⱼ Aⱼ · f(cᵢⱼ)`, deterrence `f(c)=e^(−β c)`, `cᵢⱼ` = networkx travel-time skim between zone connectors → **seed OD matrix**.
3. **Mode choice** — MVP stub: car-only share × PCU (identity). Logit is the documented upgrade.
4. **Assignment** — load OD demand into UXsim (`adddemand` between zone connector nodes), `exec_simulation()`,
   read `analyzer.link_to_pandas()['traffic_volume']` per link. With `tmax = 3600 s`, link volume ≈ vph.

### 6.3 Calibration — ODME (req 5)
Primary method: **path-based multiplicative OD adjustment** (a transparent, textbook ODME heuristic):

```
repeat (max N iters):
    sim   = UXsim_assign(T)                         # simulated vph at every link
    GEH_c = GEH(sim_c, obs_c)  for each counter c
    for each counter link c:  ratio_c = obs_c / max(sim_c, ε)
    for each OD pair (i,j):   factor_ij = clamp( geomean{ ratio_c : c on shortest_path(i,j) }, 0.5, 2.0 )
    T = T ⊙ factor                                  # elementwise
until mean GEH converges
```

- Path↔counted-link incidence is precomputed from free-flow shortest paths (networkx).
- **Scoring:** `GEH = √( 2(M−C)² / (M+C) )` with `M` = modeled vph, `C` = counted vph.
  Acceptance criterion reported: **GEH < 5 for ≥ 85 % of counters**, plus RMSE and mean GEH.
- Alternative method (noted, not built): gravity-`β` + production-scaling via **SPSA**.

## 7. API surface (`:8100`)

```
GET    /healthz
# scenarios
POST   /scenarios                         GET /scenarios            GET/DELETE /scenarios/{id}
POST   /scenarios/demo                    # build a full self-contained demo scenario (offline)
# network
POST   /scenarios/{id}/network/import-osm     {south,west,north,east}
POST   /scenarios/{id}/network/load-sample    {rows,cols,...}
POST   /scenarios/{id}/network/import-geojson
GET    /scenarios/{id}/network                # GeoJSON FeatureCollection (nodes + links)
# zones
GET/POST /scenarios/{id}/zones            POST /scenarios/{id}/zones/auto      PATCH /zones/{id}
# counters (georeference bridge)
GET    /counter-sources                       # proxy: traffic-counter projects→videos→lines(+counts)
POST   /scenarios/{id}/counters               {lat,lon,source_video_id,source_line_id,link_direction}
POST   /scenarios/{id}/counters/{cid}/pull-observations
GET    /scenarios/{id}/counters               # GeoJSON with observed_vph
# 4-step
POST   /scenarios/{id}/trip-generation
POST   /scenarios/{id}/trip-distribution      {beta}
POST   /scenarios/{id}/assignment             {od_matrix_id}
POST   /scenarios/{id}/run-4step              {beta}
# calibration + results
POST   /scenarios/{id}/calibrate              {max_iters}
GET    /scenarios/{id}/calibration/{run_id}
GET    /scenarios/{id}/results/link-flows     # GeoJSON links: sim_vph, obs_vph, geh, ratio
GET    /scenarios/{id}/results/summary        # KPIs
```

The traffic-counter client (`counter_client.py`) mirrors `frontend/api_client.py` and consumes the
documented endpoints `GET /projects`, `GET /projects/{id}/videos`, `GET /videos/{id}/lines`,
`POST /videos/{id}/counts`, `GET /videos/{id}`.

## 8. Frontend (Solara + ipyleaflet, `:8866`)

One page: an ipyleaflet `Map` (OSM tiles best-effort; GeoJSON layers render offline) plus a
left **step sidebar** mirroring the Visum workflow:

1. **Scenario** — select/create (or "Load demo").
2. **Network** — draw a bbox → *Import OSM*, or *Load sample*; render nodes/links.
3. **Zones** — draw/auto-generate TAZ; set production/attraction.
4. **Counters** — pick a counting line (from `/counter-sources`), click the map to place it
   (snaps to nearest link), set direction, *Pull observations*.
5. **Run 4-step** — generation / distribution / assignment (or *Run all*); OD heatmap + link choropleth.
6. **Calibrate** — run ODME; show GEH/RMSE convergence, before/after link coloring
   (green GEH<5 / amber 5–10 / red >10), and an observed-vs-modeled scatter.

## 9. Running it

```bash
# build & start the model stack (DB + API + UI)
docker compose up --build macromodel-db macromodel-api macromodel-ui

# offline end-to-end demo (no internet, no live counter needed)
docker compose exec macromodel-api python seed_demo.py
#   → builds a demo scenario, runs the 4-step model, calibrates, prints before/after GEH

# UI
open http://localhost:8866          # Solara map app
open http://localhost:8100/docs     # API docs

# unit tests
docker compose exec macromodel-api pytest -q
```

For **live** integration, also bring up the counter stack (`api` service); `GET /counter-sources`
then lists real videos/lines and *Pull observations* fetches real volumes.

## 10. Acceptance criteria (MVP "done")

- `pytest` green: gravity row/col totals, GEH formula, **ODME recovers a synthetic ground-truth OD**,
  netconvert builds a runnable UXsim `World`, PCU/direction mapping correct.
- `seed_demo.py` runs end-to-end offline and shows **mean GEH dropping** with **≥85 % of counters
  reaching GEH < 5** after calibration.
- The Solara UI walks the 6 steps on the demo scenario and the link choropleth recolors red→green.
