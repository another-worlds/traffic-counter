# Dataflow: Frontend To API

Status: [DONE]

This flow covers the Streamlit workspace selector, list views, line editor pages, download actions, and the embedded hybrid overlay host boundary.

```mermaid
flowchart LR
    Sidebar[frontend/sidebar.py] --> Client[frontend/api_client.py]
    Pages[frontend/pages/*] --> Client
    CountPage[frontend/pages/2_Count_and_export.py] --> Overlay[React/Vite custom component]
    Overlay --> CountPage
    Client -->|GET /projects| Projects[api/routers/projects.py]
    Client -->|GET/POST /projects/{id}/videos| Videos[api/routers/videos.py]
    Client -->|GET/POST/PATCH /projects/{id}/lines| Lines[api/routers/lines.py]
    Client -->|POST /projects/{id}/counts| Analysis[api/routers/analysis.py]
    Client -->|POST /projects/{id}/export| Analysis
    Client -->|GET /worker/status| WorkerStatus[api/routers/worker.py]
    Projects --> DB[(PostgreSQL)]
    Videos --> DB
    Lines --> DB
    Analysis --> DB
    WorkerStatus --> DB
    Videos --> Store[(Storage backend)]
    Analysis --> Store
    Overlay -->|viewport events + line edits| Client
```

## Module Boundaries

- `frontend/api_client.py` is a thin HTTP wrapper and must not own business logic.
- `frontend/sidebar.py` is the shared UI orchestration surface for workspace selection and quick export.
- `frontend/pages/1_🎥_Videos.py` and `frontend/pages/2_📏_Count_and_export.py` are view controllers, not data processors.
- The React/Vite overlay owns the synchronized viewport, line dragging, line layer toggles, and local edit state.
