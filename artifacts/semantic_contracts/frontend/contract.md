# Frontend Semantic Contract

Status: [DONE]

## Scope

The frontend is the operator interface for workspace selection, video upload, line drawing, analysis status, counting, and export.

## Responsibilities

- Render workspace state from the API.
- Submit project, video, and line actions through the API client.
- Present worker progress and analyzed-video summaries.
- Never compute counts locally.
- Host the embedded React/Vite overlay for the advanced counting-line editor while preserving Streamlit as the page shell.

## Invariants

- The UI must stay stateless with respect to persistent domain data.
- All persistent mutations go through `frontend/api_client.py`.
- Workspace selection is stored only in Streamlit session state.

## Dependencies

- `frontend/api_client.py` for HTTP calls.
- `frontend/sidebar.py` for shared navigation and quick actions.
- `frontend/pages/*` for page-specific flows.
- `frontend/pages/2_Count_and_export_hybrid.py` for the hybrid overlay host page.
