# API Semantic Contract

Status: [DONE]

## Scope

The API is the authoritative state-management and orchestration layer.

## Responsibilities

- Create, list, inspect, and delete projects.
- Upload and list videos per project.
- Queue analyses and expose worker progress.
- Create, update, and delete counting lines.
- Compute counts and generate XLSX exports from stored tracks.

## Invariants

- Every video belongs to exactly one project.
- Every counting line belongs to exactly one project.
- Count and export operations only operate on analyzed videos.
- The API must not assume the worker has local filesystem access; storage may be local or GCS.

## Inputs And Outputs

- Inputs: HTTP requests, database rows, storage artifact references.
- Outputs: JSON responses, file-stream responses, worker launch requests, storage reads.

## Key Dependencies

- Database session provider.
- Shared storage key helpers.
- Job runner abstraction.
- Track loading, counting, suggestion, heatmap, and XLSX services.
