# Dataflow: Counting And Export

Status: [DONE]

This flow covers line selection, track loading, count computation, and XLSX generation.

```mermaid
flowchart LR
    UI[frontend count/export page] --> API[api/routers/analysis.py]
    API --> DB[(PostgreSQL)]
    API --> Tracks[api/services/tracks.py]
    Tracks --> Store[(Storage backend)]
    API --> Count[api/services/counting.py]
    API --> Export[api/services/xlsx_export.py]
    Export --> Response[StreamingResponse XLSX]
```

## Rules

- All selected videos must belong to the same project.
- All selected videos must already be analyzed before counts are computed.
- Counting is purely derived from stored track data and line geometry; the worker is not involved.
