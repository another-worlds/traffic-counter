# macromodel

An open, Dockerised **macroscopic traffic-modelling** app — a lightweight cousin of
PTV Visum that turns the **traffic-counter** app's directional vehicle counts into a
calibrated **4-step** transport model. See [`SPEC.md`](./SPEC.md) for the full design.

```
counts (traffic-counter API) ──▶ georeference on a map ──▶ 4-step model (UXsim) ──▶ ODME calibration
```

## Stack
- **macromodel-api** (`:8100`) — FastAPI + UXsim, the 4-step model + ODME calibration, and the
  traffic-counter REST client. `backend/`
- **macromodel-ui** (`:8866`) — Solara app hosting an **ipyleaflet** interactive map. `frontend/`
- **macromodel-db** (`:5433`) — PostGIS database (separate from the counter's DB).

## Quick start
```bash
# from the repo root
docker compose up --build macromodel-db macromodel-api macromodel-ui

# offline end-to-end demo (no internet / no live counter needed)
docker compose exec macromodel-api python seed_demo.py

# unit tests
docker compose exec macromodel-api pytest -q

# open
#   http://localhost:8866   Solara map UI
#   http://localhost:8100/docs   API docs
```

In the UI: **Load demo scenario → Run 4-step → Calibrate**, and watch the link colours go
red→green as the simulated volumes converge on the observed counts (GEH < 5).

For **live** counts, also start the counter stack (`docker compose up api`) — then
*Load counter sources* lists real videos/lines and placing a counter pulls real volumes.
