# SCCP Document Structure

This folder mirrors the repo-side SCCP structure and points to the authoritative artifact tree in `artifacts/`.

## Three Phases

1. Phase 1: Architecture
2. Phase 2: Semantic Contracts
3. Phase 3: Implementation

## Authoritative Files

- `artifacts/current_session/current_plan.md`
- `artifacts/architecture/high_level_architecture.md`
- `artifacts/architecture/dataflows/*.md`
- `artifacts/semantic_contracts/modules_overview.md`
- `artifacts/semantic_contracts/*/contract.md`

## Repo Surfaces Covered

- `frontend/`
- `api/`
- `worker/`
- shared storage and database boundaries
- `frontend/pages/2_Count_and_export_hybrid.py`
- `frontend/hybrid_viewport/` planned React/Vite component bundle

## Module Index

- [counting-line overlay](modules/counting-line-overlay.md)
