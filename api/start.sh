#!/bin/sh
set -e

# Start Uvicorn with large request size support
# Note: HTTP/1.1 clients can send up to 2^63-1 bytes theoretically,
# but we set a practical limit of 100GB for the application.
# Default to 1 worker: every worker keeps its own per-video trajectory
# cache, and running two of them doubles peak RAM on long videos with
# negligible latency benefit (the app is mostly I/O bound). Operators
# with plenty of headroom can opt back into 2 via API_WORKERS=2.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port ${PORT:-8000} \
    --workers ${API_WORKERS:-1} \
    --ws-max-size 1000000 \
    --h11-max-incomplete-event-size 20971520
