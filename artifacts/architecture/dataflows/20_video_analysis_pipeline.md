# Dataflow: Video Analysis Pipeline

Status: [DONE]

This flow covers upload, queueing, worker execution, and persistence of analysis outputs.

```mermaid
flowchart TD
    Upload[Upload video in UI] --> Post[POST /projects/{project_id}/videos]
    Post --> DB1[(videos row created)]
    Post --> Store1[(source video stored)]
    Analyze[Click Analyze] --> Queue[POST /videos/{video_id}/analyze]
    Queue --> DB2[(status = queued)]
    Queue --> Trigger[Job runner enqueues worker]
    Trigger --> Worker[worker/main.py]
    Worker --> Pipeline[worker/pipeline.py]
    Pipeline --> Read[download source video]
    Pipeline --> Detect[YOLOv8 + ByteTrack]
    Pipeline --> Parquet[tracks.parquet]
    Pipeline --> Frame[frame.jpg]
    Pipeline --> Traj[trajectories.png]
    Parquet --> Store2[(Storage backend)]
    Frame --> Store2
    Traj --> Store2
    Worker --> DB3[(video metadata updated)]
```

## Contracts

- The API marks a video as queued before worker execution starts.
- The worker writes exactly one track artifact set per video analysis run.
- Progress updates are best-effort and must never block analysis completion.
