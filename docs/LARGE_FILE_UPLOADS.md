# Large File Upload Support (>20GB)

This document describes the configuration for supporting video file uploads larger than 20GB.

## Quick Start

**For local development:**

```bash
# Create Streamlit config for large uploads
mkdir -p frontend/.streamlit
cat > frontend/.streamlit/config.toml << 'EOF'
[client]
maxUploadSize = 100000

[server]
maxUploadSize = 100000

[logger]
level = "info"
EOF

# Restart Streamlit app
```

**For Docker deployment:**

The Docker images are pre-configured via `api/start.sh` and require no additional setup.

## Configuration Changes

### 1. Streamlit Frontend (`frontend/.streamlit/config.toml`)

- `client.maxUploadSize = 100000` (100GB limit for file picker)
- `server.maxUploadSize = 100000` (100GB limit for server processing)

This allows the Streamlit file uploader to handle files up to 100GB.

### 2. Frontend Page (`frontend/pages/1_ЁЯОе_Videos.py`)

- Added file size detection
- For files >1GB: streams from `file.file` (file-like object) to avoid memory exhaustion
- For files <1GB: uses `file.getvalue()` (bytes) for backward compatibility
- Shows file size in UI for transparency

### 3. API Client (`frontend/api_client.py`)

- Updated `upload_video()` to accept both bytes and file-like objects
- httpx library handles streaming transparently for file-like objects
- No in-memory buffering for large files

### 4. API Server (`api/start.sh` and `api/Dockerfile`)

- Created startup script with Uvicorn configuration for large uploads
- `--h11-max-incomplete-event-size 20971520` (20MB) for HTTP header parsing
- Uvicorn 0.30.6 properly streams request bodies without size limits
- Multi-worker setup (2 workers) for concurrent upload handling

### 5. Storage Backend

- Backend-agnostic: both local filesystem and GCS support streaming
- `storage.upload_stream()` in `api/app/services/storage.py` handles chunked writes
- No in-memory buffering in the API

## Testing Large Uploads

### Local Testing (with Docker Compose)

```bash
# Create a 25GB test file
dd if=/dev/zero of=test_25gb.mp4 bs=1M count=25600

# Upload via Streamlit UI
# The file will stream directly to storage without loading into memory
```

### Performance Notes

- **Streaming overhead**: ~5-10% CPU increase for large uploads due to hash computation
- **Network**: Upload speed limited by network bandwidth, not application
- **Storage**: GCS transfer requires service account credentials; local storage uses available disk

## Limits and Constraints

### Hard Limits

- **Streamlit UI**: 100GB (configurable in `.streamlit/config.toml`)
- **HTTP protocol**: Theoretical limit 2^63-1 bytes, practical limit depends on storage backend

### Soft Limits (Performance)

- **Memory**: Frontend Streamlit process uses <100MB for files <1GB, streams for larger files
- **API process**: Stream-based, constant memory regardless of file size
- **Disk**: Must have sufficient free space for storage backend

## Troubleshooting

### "File too large" Error in Streamlit

- Increase `client.maxUploadSize` and `server.maxUploadSize` in `.streamlit/config.toml`
- Restart Streamlit app after changing config

### Upload Hangs or Times Out

- Check API logs: `docker logs traffic-counter-api`
- Verify network connectivity and bandwidth
- Ensure storage backend (disk or GCS) has space and isn't I/O bound

### API Memory Usage Spikes

- If using local storage, ensure sufficient disk space
- If using GCS, verify credentials and quotas
- Monitor with `docker stats` during upload

## Future Improvements

1. **Presigned URLs**: For production, use GCS presigned URLs for direct browser → cloud uploads
2. **Multipart upload**: Implement resumable uploads with progress tracking
3. **Bandwidth throttling**: Rate-limit uploads to prevent network saturation
4. **Chunked verification**: Add MD5/SHA256 checksums for upload integrity
