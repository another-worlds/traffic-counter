# ADR 0001 — Client, engine, and data architecture for macromodel's next phase

**Status:** Proposed (research-backed; awaiting decision)
**Date:** 2026-06-11
**Scope:** `macromodel` subsystem (the "lightweight cousin of PTV Visum")
**Supersedes:** nothing — this is the first ADR
**Expanded by:** [`../architecture/tooling-landscape.md`](../architecture/tooling-landscape.md) (2026-06-12) —
a ground-up, ~20-segment open-source tool/gap landscape + integration diagram. It **completes the §7
verification pass** this ADR flagged as unfinished, and **corrects §4.2's "no engine provides ODME"**: a
turnkey OSS ODME *does* exist in the GMNS lane (Path4GMNS), now used as a reference oracle.

---

## 1. Context

The MVP described in [`../../SPEC.md`](../../SPEC.md) is **already built and working**:

| Layer | As-built MVP |
|---|---|
| Engine | **UXsim** (mesoscopic dynamic) doing trip generation → gravity/Furness distribution → assignment |
| Calibration | **path-based multiplicative ODME** (free-flow shortest-path incidence), scored with **GEH** |
| Frontend | **Solara + ipyleaflet**, one page, a 6-step sidebar that already mirrors the Visum workflow |
| Database | **PostGIS image**, but geometry stored as **GeoJSON in JSON columns**; spatial work in shapely/networkx/pyproj |
| Integration | REST bridge that turns traffic-counter directional counts into directional link volumes (vph/PCU) |

This ADR records an architecture **direction for the next phase**, informed by a multi-agent
research effort (mid-2026). The driving question the research answered:

> What is the best-fit open-source toolkit and UX for evolving macromodel toward a richer
> "basic Visum-interface clone with a remote backend" — and specifically, is the right client a
> **QGIS plugin** talking to our server, or a **web app**?

**Important framing:** the research evaluated a *target* stack (AequilibraE, QGIS/QAequilibraE,
Martin/MapLibre, web docking UIs). It does **not** describe the current code. Several
recommendations below are therefore *migrations to evaluate*, not descriptions of the status quo.
In particular, we explicitly resist swapping the working UXsim pipeline for AequilibraE without a
deliberate cost/benefit decision (see §4.2).

---

## 2. The pillars and the question

1. **Client / UX** — how to present a Visum-like workflow over a remote backend.
2. **Modelling engine** — what computes distribution + assignment, and where ODME lives.
3. **Data / backend / rendering** — single source of truth and how geometry reaches the map.

Plus a previously-parked decision: **the edit boundary** — who edits the canonical data, and how.

---

## 3. Findings (with confidence and sources)

Confidence tags are the research agents' own grades. **The dedicated adversarial verification
pass did not complete** (killed by a session limit), so the highest-confidence items below rest on
**natural cross-corroboration** — independent agents reaching the same primary source. This is
noted as "[Nx]" = corroborated by N independent agents. See §7 for the verification gap.

### 3.1 Client / UX

- **QAequilibraE (the AequilibraE QGIS plugin) is local-SpatiaLite-only — no remote/server/PostGIS/HTTP-API capability of any kind.** [HIGH] [2x]
  — https://plugins.qgis.org/plugins/qaequilibrae/ , https://www.aequilibrae.com/develop/qgis/getting_started.html
- **Any QGIS Desktop plugin must be GPL-v2-or-later with source available** (plugin-repo policy; importing PyQGIS makes a plugin a GPL derivative). [HIGH] [2x]
  — https://blog.qgis.org/2016/05/29/licensing-requirements-for-qgis-plugins/ , https://plugins.qgis.org/docs/publish
- **QGIS→server sync stacks (Mergin/geodiff, QFieldCloud deltas) exist for *offline fieldwork*** — needless complexity for an always-online modelling app, which QGIS's live PostGIS editing already avoids. [MEDIUM]
  — https://github.com/MerginMaps/geodiff , https://docs.qfield.org/reference/qfieldcloud/system/
- **No open-source project ships a Visum-style integrated desktop suite.** Every actively-developed transport UX (Conveyal, SimWrapper, OpenTripPlanner) is **web-over-a-compute-server**; the only healthy open-source desktop editor (SUMO netedit) is single-purpose and a whole product in itself. [MEDIUM→HIGH]
  — https://github.com/conveyal/analysis-ui , https://github.com/simwrapper/simwrapper , https://sumo.dlr.de/docs/Netedit/index.html
- **Browser docking UX is proven** (JupyterLab/Lumino, vscode.dev) and **Dockview is the one clearly-healthy library** — MIT, zero-dep, floating groups + popout-to-new-window + layout serialization, releasing weekly through May 2026 (v6.6.1). Golden Layout is dormant (no release since v2.6.0, 2022). [HIGH] [3x]
  — https://github.com/mathuo/dockview , https://dockview.dev/ , https://github.com/jupyterlab/lumino
- **Decisive negative finding:** every transport *incumbent* — PTV (Lines/Flows/Model2Go), Bentley OpenPaths, Esri (ArcGIS Online vs Pro), Via/Remix, Replica, StreetLight — keeps the docking IDE on the desktop and ships a **simplified map/dashboard UX** on the web. None ships a Visum-style browser docking IDE. [MEDIUM, inferred across ~8 products]
  — https://www.ptvgroup.com/en-us/products/ptv-lines , https://www.bentley.com/software/openpaths/ , https://ridewithvia.com/solutions/remix

### 3.2 Engine

- **AequilibraE v1.6.2 (Apr 2026), MIT + a one-line attribution clause.** [HIGH]
  — https://pypi.org/project/aequilibrae/ , https://github.com/AequilibraE/aequilibrae/blob/develop/LICENSE.TXT
- **AequilibraE has NO native ODME** — confirmed it would be our build either way. [HIGH] [2x]
  — https://www.aequilibrae.com/develop/python/traffic_assignment/assignment_procedures.html
- **But every ODME building block is exposed:** BFW equilibrium assignment, **select-link analysis returning per-link OD-contribution matrices**, skims, path computation. A Spiess-style gradient or proportional ODME is buildable on top. [HIGH]
  — https://www.aequilibrae.com/develop/python/traffic_assignment/assignment_procedures.html , https://github.com/AequilibraE/aequilibrae/issues/493
- **Native gravity + IPF/Furness** distribution. [HIGH]
  — https://www.aequilibrae.com/docs/python/V.1.1.0/_auto_examples/trip_distribution/
- **No PostGIS connector** — AequilibraE's project is a SpatiaLite file; matrices are OMX; import is OSM/GMNS/CSV. A per-run **PostGIS → AequilibraE-project materialization step** would be required. [HIGH]
  — https://www.aequilibrae.com/latest/python/aequilibrae_project.html

### 3.3 Data / rendering

- **For serving editable PostGIS data to MapLibre, Martin is the pick** (MapLibre org; auto-publishes tables + PostgreSQL function sources with `z/x/y` + JSON params returning MVT). [HIGH] [2x]
  — https://github.com/maplibre/martin , https://maplibre.org/martin/sources-pg-functions
- **QGIS Server serves only raster** (WMS/WMTS); vector tiles need an immature third-party plugin — so QGIS Server is *not* a tile engine for us, only worth it to reuse `.qgs` cartography (which a web app won't have). [HIGH]
  — https://docs.qgis.org/3.40/en/docs/server_manual/faq.html , https://github.com/3liz/qgis-server-tiles-plugin
- **Static PMTiles is the wrong tool for editable data** (any edit forces an archive rebuild). [HIGH]
  — https://docs.protomaps.com/pmtiles/

### 3.4 Edit boundary (concurrency)

- **QGIS Desktop edits PostGIS directly and natively — no plugin, no sync layer.** [HIGH]
  — https://docs.qgis.org/3.40/en/docs/user_manual/managing_data_source/opening_data.html
- **But concurrency is last-write-wins with no native locking;** multi-user safety needs DB-side controls, the pgVersion plugin, or API-mediated writes. Buffered Transaction Groups (QGIS 3.26+) reduce lock duration. [HIGH] [2x]
  — https://github.com/qgis/QGIS/issues/11031 , https://www.opengis.ch/2022/07/26/high-efficiency-with-buffered-transactional-editing-in-qgis/

### 3.5 UX patterns

- Visum's productive ideas worth keeping: **linked lists (map↔table brushing)**, the **re-runnable procedure/calculation sequence**, and matrix views. Its liabilities (steep curve, dated multi-window default) are what the incumbents drop on web. [HIGH/MEDIUM]
- **Wizards suit prescribed-order, infrequent tasks** (NN/g) — right for a guided model run — **but experts need an accelerator/escape hatch** (Nielsen heuristic #7): default-reuse + a procedure-sequence canvas for daily users. [HIGH]
  — https://www.nngroup.com/articles/wizards/ , https://www.nngroup.com/articles/flexibility-efficiency-heuristic/
- **Long compute (minutes):** percent-done + rough ETA + cancel; run-in-background with a salient completion toast and a run-history/status page (NN/g 10-second rule). [HIGH]
  — https://www.nngroup.com/articles/response-times-3-important-limits/ , https://www.nngroup.com/articles/designing-for-waits-and-interruptions/
- **Linked brushing** via deck.gl's GPU `BrushingExtension`; data grids (AG Grid Community, MIT) for Visum-like lists; **command palette** (cmdk) for experts. [HIGH]
  — https://deck.gl/docs/api-reference/extensions/brushing-extension , https://www.ag-grid.com/javascript-data-grid/community-vs-enterprise/

---

## 4. Decisions

### 4.1 Client — **web app, not a QGIS plugin; simplified-first, expert workspace opt-in**

- **Reject the QGIS-plugin client.** It is GPL-encumbered, QAequilibraE is local-only (no remote
  backend), and the sync stacks are built for offline fieldwork — a poor fit for an always-online
  modelling tool. We would also have to build the procedure/matrix UI in PyQt regardless.
- **Keep the current simplified, guided UX** (already realised in the Solara 6-step sidebar). The
  incumbent evidence is unanimous: lead simplified on the web.
- **When richer needs arrive** — full-network rendering at scale, an expert docking workspace,
  Visum-grade lists/matrices — **migrate the frontend to React + MapLibre GL JS + deck.gl
  (+ flowmap.gl) + AG Grid Community + Dockview**, all MIT/BSD. Make docking/floating panels an
  **opt-in expert workspace**, never the default front door.

### 4.2 Engine — **keep UXsim for now; evaluate AequilibraE for the static-assignment + ODME core**

- **Do not reflexively swap the working UXsim pipeline.** It already delivers 4-step + ODME + GEH
  and is tested.
- **Evaluate migrating the static-assignment + ODME core to AequilibraE** for one concrete,
  specific gain: **BFW user-equilibrium assignment plus exact select-link OD→link incidence**,
  which is a more rigorous basis for ODME than the current free-flow shortest-path incidence
  approximation. This is a **planned spike**, not a settled swap (see §6).
- Either way, **ODME stays our own module** (no engine provides it) — keep it engine-agnostic so it
  can sit on UXsim *or* AequilibraE select-link matrices.

### 4.3 Data / rendering — **PostGIS as source of truth; native geometry + Martin when we move to vector tiles; drop QGIS Server**

- Promote geometry from **GeoJSON-in-JSON to native PostGIS columns + GiST** (already the SPEC's
  documented upgrade path) when moving beyond MVP-size networks.
- Serve geometry to the map via **Martin → MapLibre vector tiles** at that point. For MVP-size
  networks, the current GeoJSON-over-ipyleaflet is adequate.
- **Drop QGIS Server from consideration** — raster-only, no vector-tile fit.

### 4.4 Edit boundary — **single source of truth in PostGIS; app writes through FastAPI; QGIS-direct as the free power-user side door**

- **PostGIS is the one source of truth.** The app performs all standard editing **through the
  FastAPI layer** (already the case), so validation, auth, and conflict handling live in one place.
- **Power users who need heavy GIS editing point QGIS Desktop straight at the same PostGIS schema**
  — free, no plugin to write or maintain, no GPL exposure. This is the near-free "hybrid": one
  schema-doc page instead of a GPL plugin.
- **Concurrency mitigations:** integer primary keys everywhere; Buffered Transaction Groups for
  QGIS sessions; add an **optimistic-lock (version column) at the API layer** if concurrent editing
  of the same tables becomes a hard requirement.

### 4.5 UX direction — **"modern simplified core + opt-in expert workspace"**

- **Default:** guided run-stepper + clean map + results dashboard (GEH, counts-vs-modeled scatter,
  link-volume choropleth / bandwidth flows, before/after recolour) — exactly the shape already in
  the Solara MVP.
- **Expert workspace (later, on the web stack):** Dockview dockable/floating panels hosting linked
  lists (AG Grid), a matrix editor, and a **re-runnable procedure-sequence panel** — Visum
  muscle-memory, opt-in.
- Map↔table linked brushing throughout; command palette for power users; background jobs with
  ETA + cancel + run history.

---

## 5. Consequences

**Positive**
- Entire target stack is MIT/BSD — IP options stay open (no GPL plugin).
- Reuses the working MVP; no rip-and-replace. Migrations are staged and optional.
- One source of truth, with a free QGIS power-user editing path and no bespoke sync layer.

**Negative / costs**
- The eventual React/MapLibre frontend is a real re-platform from Solara/ipyleaflet.
- An AequilibraE migration adds a PostGIS→AequilibraE-project materialization step.
- **Browser network-topology editing (snap/split/merge) is the single biggest web-path risk** —
  `@deck.gl-community/editable-layers` maturity is unproven for this; QGIS-direct-edit is the
  documented fallback.

---

## 6. Open questions / planned spikes

1. **Browser link-editing spike** — can `@deck.gl-community/editable-layers` do snap/split/merge well
   enough for MVP? (De-risks §4.1's biggest unknown.)
2. **UXsim vs AequilibraE bake-off** — prototype the ODME module against AequilibraE select-link
   matrices on a toy network; compare accuracy/effort against the current UXsim path-incidence ODME
   before committing to §4.2.
3. **Data-contract design** — the native-PostGIS schema as single source of truth, plus the
   materialization step, before any engine migration.

---

## 7. Verification status (honesty note)

> **Update (2026-06-12):** the verification pass has since been **run** — see
> [`tooling-landscape.md` §8](../architecture/tooling-landscape.md). All eight load-bearing claims were
> re-checked against primary source code (verdicts: 7× CONFIRMED, 1× PARTIAL). The one correction: "no
> engine provides ODME" holds for the AequilibraE/OMX lane but **not** for the GMNS lane — Path4GMNS ships
> a turnkey `conduct_odme`. The original honesty note is retained below for the record.

The formal adversarial verification pass **was killed by a session limit before producing output**.
The decision-critical claims in §3 are instead supported by **natural cross-corroboration** (the
"[Nx]" tags) — independent research agents hitting the same primary sources. Two UX sub-threads
(calibration-viz deep-dive, desktop-TDM deep-dive) were also cut off, but the wizard/progress and
geo-UX threads cover enough to act on. **Recommended before locking in §4.2/§4.3:** run the formal
verification on the eight load-bearing claims. Other open flags: AequilibraE's exact 2026 release
dates (PyPI vs GitHub conflict, resolved in favour of PyPI); a few npm download figures are
secondary.

---

## 8. Full source list

**AequilibraE / QAequilibraE / engine**
- https://pypi.org/project/aequilibrae/
- https://www.aequilibrae.com/develop/python/traffic_assignment/assignment_procedures.html
- https://github.com/AequilibraE/aequilibrae/issues/493
- https://github.com/AequilibraE/aequilibrae/blob/develop/LICENSE.TXT
- https://www.aequilibrae.com/latest/python/aequilibrae_project.html
- https://plugins.qgis.org/plugins/qaequilibrae/
- https://www.aequilibrae.com/develop/qgis/getting_started.html

**QGIS / PostGIS / licensing**
- https://blog.qgis.org/2016/05/29/licensing-requirements-for-qgis-plugins/
- https://plugins.qgis.org/docs/publish
- https://docs.qgis.org/3.40/en/docs/user_manual/managing_data_source/opening_data.html
- https://github.com/qgis/QGIS/issues/11031
- https://www.opengis.ch/2022/07/26/high-efficiency-with-buffered-transactional-editing-in-qgis/
- https://docs.qgis.org/3.40/en/docs/server_manual/faq.html
- https://github.com/3liz/qgis-server-tiles-plugin
- https://github.com/MerginMaps/geodiff
- https://docs.qfield.org/reference/qfieldcloud/system/

**Rendering / tiles**
- https://github.com/maplibre/martin
- https://maplibre.org/martin/sources-pg-functions
- https://docs.protomaps.com/pmtiles/

**Web UI stack / docking / grids / brushing**
- https://github.com/mathuo/dockview , https://dockview.dev/
- https://github.com/jupyterlab/lumino
- https://github.com/caplin/FlexLayout
- https://github.com/golden-layout/golden-layout
- https://deck.gl/docs/api-reference/extensions/brushing-extension
- https://github.com/visgl/flowmap.gl
- https://www.ag-grid.com/javascript-data-grid/community-vs-enterprise/
- https://github.com/pacocoursey/cmdk

**Existing transport platforms**
- https://github.com/conveyal/analysis-ui , https://docs.conveyal.com/analysis/regional
- https://github.com/simwrapper/simwrapper
- https://sumo.dlr.de/docs/Netedit/index.html
- https://www.ptvgroup.com/en-us/products/ptv-lines
- https://www.bentley.com/software/openpaths/
- https://ridewithvia.com/solutions/remix

**UX guidance**
- https://www.nngroup.com/articles/wizards/
- https://www.nngroup.com/articles/flexibility-efficiency-heuristic/
- https://www.nngroup.com/articles/response-times-3-important-limits/
- https://www.nngroup.com/articles/progress-indicators/
- https://www.nngroup.com/articles/designing-for-waits-and-interruptions/
- https://www.nngroup.com/articles/progressive-disclosure/
- https://carbondesignsystem.com/components/progress-indicator/usage/
