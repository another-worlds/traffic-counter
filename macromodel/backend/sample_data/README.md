# Sample data (offline demo)

The MVP demo network is generated programmatically (a 4×4 grid with real lat/lon) by
`app.services.osm_import.build_sample_network`, so there is no large static fixture to
ship. The full self-contained demo — network + zones + **synthetic counters whose
observed volumes come from a known ground-truth OD** — is built by
`app.services.demo.build_demo`, exposed as `POST /scenarios/demo` and driven by
`seed_demo.py`.

This lets the entire pipeline (network → zones → georeferenced counts → 4-step →
calibration) run end-to-end with **no internet and no live traffic-counter**, while the
real path (`POST /scenarios/{id}/network/import-osm` + counter *pull-observations*) uses
OSMnx and the traffic-counter REST API.

To export the sample as GeoJSON for the *Upload GeoJSON* path, call
`GET /scenarios/{id}/network` after `load-sample`.
