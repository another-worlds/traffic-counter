"""tus 1.0.0 resumable upload endpoint (core + creation + termination extensions).

Protocol: https://tus.io/protocols/resumable-upload.html

Flow:
  POST   /tus/files              Create upload session → 201 + Location
  HEAD   /tus/files/{id}         Resume: returns Upload-Offset (bytes received so far)
  PATCH  /tus/files/{id}         Append chunk (application/offset+octet-stream)
  DELETE /tus/files/{id}         Cancel upload and clean up
  GET    /tus/files/{id}/result  Poll for finalized Video record (after PATCH completes)
"""
from __future__ import annotations

import base64
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Project, TusUpload, Video
from ..schemas import VideoOut
from ..services.storage import get_storage, key_video

router = APIRouter(prefix="/tus", tags=["tus"])

_TUS_RESUMABLE = "1.0.0"
_TUS_HEADERS = {
    "Tus-Resumable": _TUS_RESUMABLE,
    "Tus-Version": _TUS_RESUMABLE,
    "Tus-Extension": "creation,termination",
    "Tus-Max-Size": str(200 * 1024 ** 3),  # 200 GB
    "Access-Control-Expose-Headers": (
        "Location, Upload-Offset, Upload-Length, "
        "Tus-Resumable, Tus-Version, Tus-Extension, Tus-Max-Size, "
        "Traffic-Counter-Video-Id"
    ),
}


def _decode_metadata(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for pair in raw.split(","):
        parts = pair.strip().split(" ", 1)
        if len(parts) == 2:
            try:
                result[parts[0]] = base64.b64decode(parts[1]).decode()
            except Exception:
                result[parts[0]] = ""
        elif len(parts) == 1:
            result[parts[0]] = ""
    return result


@router.options("/files")
def tus_options():
    return Response(status_code=204, headers=_TUS_HEADERS)


@router.post("/files", status_code=201)
def tus_create(request: Request, db: Session = Depends(get_db)):
    upload_length = request.headers.get("Upload-Length")
    if not upload_length:
        raise HTTPException(400, "Missing Upload-Length header")
    try:
        upload_length_int = int(upload_length)
    except ValueError:
        raise HTTPException(400, "Invalid Upload-Length")

    metadata = _decode_metadata(request.headers.get("Upload-Metadata", ""))
    filename = metadata.get("filename", "video.mp4")
    project_id = metadata.get("project_id", "")

    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")

    # Create placeholder Video record immediately so callers can poll status.
    v = Video(
        project_id=project_id,
        filename=filename,
        storage_path="",
        status="uploading",
    )
    db.add(v)
    db.flush()
    v.storage_path = key_video(project_id, v.id, filename)

    tu = TusUpload(
        project_id=project_id,
        video_id=v.id,
        filename=filename,
        upload_length=upload_length_int,
    )
    db.add(tu)
    db.flush()  # get tu.id before commit

    v.tus_upload_id = tu.id
    db.commit()
    db.refresh(tu)

    # Touch the .part file so HEAD can stat it immediately.
    part = get_storage().tus_part_path(tu.id)
    if not part.exists():
        part.touch()

    base_url = str(request.base_url).rstrip("/")
    location = f"{base_url}/tus/files/{tu.id}"
    return Response(
        status_code=201,
        headers={
            **_TUS_HEADERS,
            "Location": location,
            "Upload-Offset": "0",
            "Traffic-Counter-Video-Id": str(v.id),
        },
    )


@router.head("/files/{upload_id}")
def tus_head(upload_id: str, db: Session = Depends(get_db)):
    tu = db.get(TusUpload, upload_id)
    if not tu:
        raise HTTPException(404, "upload not found")
    part = get_storage().tus_part_path(upload_id)
    offset = part.stat().st_size if part.exists() else 0
    return Response(
        status_code=200,
        headers={
            **_TUS_HEADERS,
            "Upload-Offset": str(offset),
            "Upload-Length": str(tu.upload_length),
            "Cache-Control": "no-store",
        },
    )


@router.patch("/files/{upload_id}")
async def tus_patch(upload_id: str, request: Request, db: Session = Depends(get_db)):
    tu = db.get(TusUpload, upload_id)
    if not tu:
        raise HTTPException(404, "upload not found")

    content_type = request.headers.get("Content-Type", "")
    if "application/offset+octet-stream" not in content_type:
        raise HTTPException(415, "Content-Type must be application/offset+octet-stream")

    try:
        client_offset = int(request.headers["Upload-Offset"])
    except (KeyError, ValueError):
        raise HTTPException(400, "Missing or invalid Upload-Offset header")

    storage = get_storage()
    part = storage.tus_part_path(upload_id)
    current_offset = part.stat().st_size if part.exists() else 0

    if client_offset != current_offset:
        raise HTTPException(409, f"Upload-Offset mismatch: expected {current_offset}, got {client_offset}")

    # Stream chunk to disk in 1 MB slices to avoid loading the whole chunk into RAM.
    chunk_size = 1024 * 1024
    written = 0
    with open(part, "ab") as f:
        async for piece in request.stream():
            f.write(piece)
            written += len(piece)

    new_offset = current_offset + written
    extra_headers: dict[str, str] = {}

    if new_offset >= tu.upload_length:
        # Finalize: move .part → storage, update Video record.
        v = db.get(Video, tu.video_id) if tu.video_id else None
        if v:
            storage.finalize_tus_upload(upload_id, v.storage_path)
            v.status = "uploaded"
            v.size_bytes = new_offset
            v.created_at = datetime.utcnow()
            db.delete(tu)
            db.commit()
            extra_headers["Traffic-Counter-Video-Id"] = str(v.id)
        else:
            storage.delete_tus_part(upload_id)
            db.delete(tu)
            db.commit()

    return Response(
        status_code=204,
        headers={**_TUS_HEADERS, "Upload-Offset": str(new_offset), **extra_headers},
    )


@router.delete("/files/{upload_id}", status_code=204)
def tus_delete(upload_id: str, db: Session = Depends(get_db)):
    tu = db.get(TusUpload, upload_id)
    if not tu:
        raise HTTPException(404, "upload not found")
    # Delete the placeholder Video if still in uploading state.
    if tu.video_id:
        v = db.get(Video, tu.video_id)
        if v and v.status == "uploading":
            db.delete(v)
    get_storage().delete_tus_part(upload_id)
    db.delete(tu)
    db.commit()
    return Response(status_code=204, headers=_TUS_HEADERS)


@router.get("/files/{upload_id}/result", response_model=VideoOut)
def tus_result(upload_id: str, db: Session = Depends(get_db)):
    """Return the Video record for a finalized (or in-progress) tus upload."""
    v = db.query(Video).filter(Video.tus_upload_id == upload_id).first()
    if not v:
        raise HTTPException(404, "no video associated with this upload id")
    return v
