# AGENTS.md — orientation for coding agents & developers

> Canonical guide for working in this repo. Claude Code reads `CLAUDE.md`, which
> `@`-imports this file, so both Claude and other agents/tools see one source of truth.
> Keep it under ~200 lines, terse, and accurate. Last reviewed: 2026-06-14.

## What this repo is — three subsystems

| Dir | Subsystem | Status |
|---|---|---|
| root: `api/` `worker/` `watcher/` `frontend/` `config/` `infra/` `scripts/` | **traffic-counter** — vehicle counting from video (YOLOv8 + ByteTrack), FastAPI + Streamlit, GPU worker. See root `README.md`. | maintained |
| `macromodel/` | **legacy macromodel** — macroscopic 4-step model app (Solara + UXsim + FastAPI, geometry as GeoJSON-in-JSON). See `macromodel/README.md`, `macromodel/SPEC.md`. | **frozen** |
| `platform/` | **the rewrite** — the macromodel rebuilt onto the target architecture with **native PostGIS** geometry. Independent of the legacy stack. See `platform/README.md`. | **active** |

> Naming: `platform/` **is** the macromodel rebuild. It is named `platform/` to avoid
> confusion with the frozen `macromodel/`. The architecture it implements is
> `macromodel/docs/architecture/tooling-landscape.md` (the north-star report).

## Golden rules

- **Active work is in `platform/`. Start there.**
- **Do NOT modify the legacy `macromodel/` app or the `traffic-counter` core** unless
  explicitly asked. The rewrite runs in parallel until it reaches parity; only then is the
  legacy app retired. Rationale: `macromodel/docs/adr/0002-clean-slate-rewrite-on-target-architecture.md`.
- Never commit to `main`. Work on a feature branch; open a PR only when asked.
- Don't bake secrets or model identifiers into committed files.

## Build / run / test

### platform/ (the rewrite) — needs Docker + PostGIS
```bash
docker compose up -d --build model-db core-api   # API → :8200 (/docs), PostGIS → :5434
curl localhost:8200/healthz
# Contract tests require REAL PostGIS (native geometry + GiST KNN):
docker compose up -d model-db
cd platform/core-api && \
  DATABASE_URL=postgresql+psycopg://model:model@localhost:5434/model python -m pytest -q tests
```

### macromodel/ (legacy — reference only, don't change)
```bash
docker compose up -d macromodel-db macromodel-api macromodel-ui   # :8100 / :8866 / :5433
cd macromodel/backend && python -m pytest -q                       # see macromodel/README.md
```

### traffic-counter (the counter app)
```bash
docker compose up -d db api frontend   # API :8000, UI :8501. (worker needs an NVIDIA GPU.)
```

## Ports (avoid collisions when adding services)

| | api | ui | db |
|---|---|---|---|
| counter | 8000 | 8501 | 5432 |
| legacy macromodel | 8100 | 8866 | 5433 |
| **platform (rewrite)** | **8200** | — | **5434** |

## Read these, don't re-derive

- North-star architecture → `macromodel/docs/architecture/tooling-landscape.md`
- Decisions (ADRs) → `macromodel/docs/adr/` (esp. `0002` — the rewrite decision)
- The rewrite's data contract → `platform/docs/data-contract.md`
- What's done / what's next → `platform/ROADMAP.md`

## Running in Claude Code on the web

The web sandbox **does not start the Docker daemon automatically**, and its egress proxy does
**TLS interception**. To prepare the `platform/` dev environment in a fresh web session:

```bash
dockerd --storage-driver=vfs >/tmp/dockerd.log 2>&1 &     # 1) start the daemon (root sandbox)
docker compose up -d model-db                              # 2) PostGIS (pulled image; no build)
python3 -m venv ~/.cache/cv \
  && ~/.cache/cv/bin/pip install -r platform/core-api/requirements.txt   # 3) host deps
cd platform/core-api && \
  DATABASE_URL=postgresql+psycopg://model:model@localhost:5434/model \
  ~/.cache/cv/bin/python -m pytest -q tests                # 4) run the contract tests
```

> **Why a host venv:** the host trusts the proxy CA (so host `pip`/`git`/`npx` work), but an
> in-container `pip install` fails cert verification unless the proxy CA is added to the image.
> The committed `platform/core-api/Dockerfile` builds normally wherever PyPI is directly
> reachable (real CI/deploy). These steps can be automated with a **SessionStart hook** + cloud
> **Setup Script** (not enabled by default — it auto-runs code each session, so opt in
> deliberately): https://code.claude.com/docs/en/claude-code-on-the-web
