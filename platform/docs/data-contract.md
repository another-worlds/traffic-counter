# Data contract — the macromodel core (foundation)

The contract that every other service in the rebuild depends on. It fixes **how geometry is
stored** (native PostGIS) and **how it crosses the API** (GeoJSON), so tiles, the modelling
engine, and the editor all interoperate without bespoke glue. Implements **segments 1–3** of
[`../../macromodel/docs/architecture/tooling-landscape.md`](../../macromodel/docs/architecture/tooling-landscape.md)
(datastore · network standard · matrix format).

## Principles

1. **Native geometry is the source of truth.** Every spatial entity has a PostGIS `geometry`
   column (SRID **4326**, WGS84 lon/lat) with a **GiST** spatial index. No GeoJSON-in-JSON.
   This is what enables `geom <-> point` KNN snapping, `ST_DWithin`, and `ST_AsMVT` tiling.
2. **GeoJSON at the boundary.** The API speaks GeoJSON geometry (RFC 7946); the service
   converts to/from native geometry. Geometry round-trips losslessly.
3. **GMNS-aligned field names.** `from_node_id`/`to_node_id`, `length_m`, `lanes`,
   `free_speed_kmh`, `capacity_vph` track the General Modeling Network Specification, so
   GMNS import/export and AequilibraE/Path4GMNS interchange are cheap.
4. **Matrices are OMX out-of-row.** OD/skim matrix *values* live in OMX (HDF5) artefacts
   referenced by `storage_ref`; the DB holds only matrix metadata. OMX is the bridge to/from
   commercial Visum/EMME.
5. **String UUID primary keys** everywhere (stable across copy-on-write scenario cloning).
6. **Directed links.** A two-way street is two `Link` rows (GMNS convention).

## Entities (this foundation)

| Table | Geometry | Key fields |
|---|---|---|
| `scenarios` | — | `id, name, description, bbox, parent_id, created_at` |
| `nodes` | `POINT` | `scenario_id, name, geom, osm_id, node_type_id` |
| `links` | `LINESTRING` | `from_node_id, to_node_id, geom, length_m, lanes, free_speed_kmh, capacity_vph, oneway, allowed_modes, link_type_id` |
| `zones` | `POLYGON` + `POINT` centroid | `geom, centroid, connector_node_id, population, workplaces, zone_type_id` |
| `connectors` | *(derived)* | `zone_id, node_id, direction, t0_min, weight` |
| `counters` | `POINT` | `geom, snapped_link_id, link_direction, source_video_id, source_line_id, observed_vph, pcu_vph` |
| `stops` | `POINT` | `geom, node_id` |
| classes | — | `link_types, node_types, zone_types` |
| PuT | — | `lines, line_route_stops` |
| demand | — | `modes, activities, demand_layers, zone_demand, mode_choice_params` |
| matrices/results | — | `matrices (OMX ref), assignment_results, calibration_runs, procedures` |

*(The non-spatial demand/procedure tables are defined now as part of the canonical contract;
their routers and engines land in later commits.)*

## API surface (this foundation)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/healthz` | liveness |
| `POST/GET/DELETE` | `/scenarios[/{id}]` | scenario CRUD |
| `POST/GET` | `/scenarios/{sid}/nodes` | node create / list |
| `POST/GET` | `/scenarios/{sid}/links` | link create (geometry derived if omitted; geodesic `length_m`) / list |
| `POST` | `/scenarios/{sid}/zones` | zone create (centroid derived from polygon if omitted) |
| `POST` | `/scenarios/{sid}/counters` | counter create |
| `GET` | `/scenarios/{sid}/network` | whole network as a GeoJSON `FeatureCollection` (each feature tagged `kind`) |
| `POST` | `/scenarios/{sid}/snap` | **KNN**: nearest link to `{lon,lat}` (GiST candidates re-ranked by geodesic distance) |

Interactive OpenAPI at `/docs`.

### Geometry examples

```jsonc
// POST /scenarios/{sid}/nodes
{ "name": "A", "geometry": { "type": "Point", "coordinates": [69.240, 41.311] } }

// POST /scenarios/{sid}/links   (geometry omitted -> straight line A->B, geodesic length)
{ "from_node_id": "<A>", "to_node_id": "<B>", "lanes": 2, "free_speed_kmh": 50 }

// POST /scenarios/{sid}/snap
{ "lon": 69.245, "lat": 41.3115 }   // -> { "link_id": "...", "distance_m": 41.2 }
```

## Deliberately deferred

- **Async** SQLAlchemy under psycopg3 (the report flags GeoAlchemy2 async as under-documented —
  the foundation uses proven **sync** sessions).
- **Alembic** migrations (greenfield uses `create_all`; no JSON-legacy to migrate).
- **Topology operations** (split/merge/snap-node/move-node) — the dedicated topology edit
  service, the largest "build" gap, comes next.
- Engine materialisation (PostGIS → GMNS/OMX → AequilibraE) and the ODME module.
