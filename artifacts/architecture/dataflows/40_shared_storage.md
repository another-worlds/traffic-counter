# Dataflow: Shared Storage Boundary

Status: [DONE]

This flow records how the API and worker stay aligned on artifact names and backend selection.

```mermaid
flowchart TB
    ApiStorage[api/services/storage.py] <--> KeyContract[shared key contract]
    WorkerStorage[worker/storage.py] <--> KeyContract
    KeyContract --> Source[source.mp4]
    KeyContract --> Tracks[tracks.parquet]
    KeyContract --> Frame[frame.jpg]
    KeyContract --> Traj[trajectories.png]
```

## Contract

- The API and worker must generate identical object keys for the same project/video pair.
- The worker produces artifacts; the API serves and derives from them.
- Storage backend selection is environment-driven and must not change the key format.
