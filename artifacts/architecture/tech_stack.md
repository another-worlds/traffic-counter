# Tech Stack

Status: [DONE]

This repository is a Python-first traffic-analysis system with three runtime surfaces and one shared persistence boundary:

- `frontend/` uses Streamlit for the operator UI.
- `api/` uses FastAPI + SQLAlchemy + PostgreSQL for project, video, line, count, and export APIs.
- `worker/` uses Ultralytics YOLOv8, ByteTrack, OpenCV, pandas, NumPy, and PyArrow to generate `tracks.parquet`, `frame.jpg`, and `trajectories.png`.
- Storage is abstracted behind a local filesystem or GCS implementation.

For the counting-line advanced UI, the frontend splits into a Streamlit shell and a React/Vite custom component that renders the interactive overlay viewport. Streamlit's custom-components framework is the canonical bridge for embedding advanced mini-applications inside Streamlit, Vite provides the fast dev server and production bundle, and React provides component/state/event handling for the overlay editor.

## Evidence From The Codebase

- `README.md` describes the stack as Streamlit frontend, FastAPI service, Cloud SQL Postgres, Cloud Storage, and a Cloud Run Job worker.
- `api/app/main.py` wires FastAPI, CORS, routers, startup DB initialization, and local file serving.
- `worker/main.py` and `worker/pipeline.py` implement the video-analysis job lifecycle.
- `frontend/streamlit_app.py`, `frontend/sidebar.py`, and `frontend/api_client.py` implement the operator UI.
- Streamlit custom-components docs describe components as a way to embed advanced UI and mini-applications inside Streamlit.
- Vite docs describe the build tool as a fast dev server plus optimized production bundler for modern web projects.
- React docs describe component composition, state, props, events, and shared data flow, which are the exact primitives needed for the overlay editor.

## Best-Practice Citations (2026-05-17)

- Streamlit Components v1 intro confirms the bi-directional component model, `components.declare_component()`, and `Streamlit.setComponentValue()` for Python <-> frontend exchange: https://docs.streamlit.io/develop/concepts/custom-components/components-v1/intro
- Streamlit Components limitations clarifies iframe isolation and reinforces that complex interaction surfaces should live inside the component boundary: https://docs.streamlit.io/develop/concepts/custom-components/components-v1/limitations
- Vite guide confirms dev server + optimized production bundle split and recommends explicit build for production deployments: https://vite.dev/guide/
- React learn docs capture the state/event model needed for interactive overlay editing flows: https://react.dev/learn

## Rationale

- Python keeps the API, worker, and UI in one language boundary, which simplifies shared schema and storage-key contracts.
- FastAPI fits the API because the service is resource-oriented and already exposes structured JSON endpoints for the Streamlit client.
- Streamlit fits the current UI because the workflow is stateful, operator-driven, and optimized for quick internal tooling.
- PostgreSQL is the correct system of record because project, video, and line state must survive worker restarts and support transactional updates.
- Parquet is the right analysis artifact for track data because it is compact, columnar, and efficient for repeated read-heavy counting/export operations.
- React is the right overlay framework because the counting-line editor needs local, responsive state for dragging, selection, toggles, synchronized frame scrubbing, and live recalculation triggers.
- Vite is the right frontend toolchain because the overlay must build into a small, hot-reloadable bundle that can be embedded as a custom Streamlit component.

## Status Legend

- [DONE] confirmed in the current repo structure
- [PLANNED] reserved for future revisions
- [LOCKED] used only after a verified implementation session
