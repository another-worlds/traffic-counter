#!/bin/sh
set -e

# Start Uvicorn with large request size support
# Note: HTTP/1.1 clients can send up to 2^63-1 bytes theoretically,
# but we set a practical limit of 100GB for the application.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port ${PORT:-8000} \
    --workers 2 \
    --ws-max-size 1000000 \
    --h11-max-incomplete-event-size 20971520
