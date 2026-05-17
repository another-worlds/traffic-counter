# Worker Semantic Contract

Status: [DONE]

## Scope

The worker is the analysis executor that converts one source video into analysis artifacts.

## Responsibilities

- Claim queued videos or process one requested video.
- Download the source video from storage.
- Run YOLOv8 + ByteTrack and emit per-frame track rows.
- Render a representative frame and trajectory overlay.
- Persist `tracks.parquet`, `frame.jpg`, and `trajectories.png`.
- Update progress and final analysis metadata in the database.

## Invariants

- A successful run must end with durable storage artifacts and an analyzed video row.
- A failed run must end with the video marked as error.
- The worker must use the same object-key contract as the API storage helpers.

## Modes

- `poll`: long-running local loop that claims queued videos.
- `single`: one-video mode used by Cloud Run Jobs.
