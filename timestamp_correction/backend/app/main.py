import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from . import counter_client
from .config import settings
from .device import log_device_status
from .schemas import (
    CorrectedCountsOut,
    CorrectedCountsRequest,
    TimestampMapOut,
    TimestampProgressOut,
    TimestampRegionIn,
    TimestampRegionOut,
    TimestampScanRequest,
    TimestampStatusOut,
    GapIntervalOut,
    LineDeltaOut,
)
from .services import gap_map, count_sync, region_service, scan_control
from .storage import get_storage, key_timestamp_region_preview, key_timestamp_status

log = logging.getLogger("timestamp_correction.api")
_executor = ThreadPoolExecutor(max_workers=1)


def _video_from_index(video_id: str, pid: str, index: dict) -> dict:
    return {
        "id": video_id,
        "project_id": pid,
        "filename": index.get("filename", "video.mp4"),
        "local_source_path": index.get("local_source_path"),
        "status": "analyzed",
    }


def _resolve_video(video_id: str, project_id: Optional[str]) -> dict:
    """Resolve video metadata without blocking on a slow counter API when possible."""
    pid = gap_map.resolve_project_id(video_id, project_id)
    index = gap_map.read_video_index(video_id) if pid else None
    if pid and index and index.get("filename"):
        return _video_from_index(video_id, pid, index)
    try:
        return counter_client.get_video(video_id, timeout=10.0)
    except httpx.TimeoutException as exc:
        if pid and index and index.get("filename"):
            return _video_from_index(video_id, pid, index)
        raise HTTPException(503, "counter API timed out — retry shortly") from exc
    except counter_client.CounterAPIError as exc:
        raise HTTPException(404, str(exc)) from exc


def create_app() -> FastAPI:
    log_device_status(settings.device)
    app = FastAPI(
        title="Timestamp Correction API",
        version="0.1.0",
        description="OSD timestamp mapping and gap-corrected vehicle counts.",
    )

    cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if cors_origins == ["*"] or not cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=".*",
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "env": settings.env}

    @app.get("/projects/{project_id}/timestamp-statuses")
    def project_timestamp_statuses(
        project_id: str,
        video_ids: Optional[str] = Query(None, description="Comma-separated video UUIDs"),
    ):
        ids = [v.strip() for v in (video_ids or "").split(",") if v.strip()]
        return gap_map.get_statuses_for_project(project_id, ids)

    @app.get("/videos/{video_id}/timestamp-status", response_model=TimestampStatusOut)
    def timestamp_status(
        video_id: str,
        project_id: Optional[str] = Query(None),
    ):
        pid = gap_map.resolve_project_id(video_id, project_id)
        if not pid:
            video = _resolve_video(video_id, project_id)
            pid = video["project_id"]
        data = gap_map.get_status(pid, video_id)
        progress = data.get("progress")
        return TimestampStatusOut(
            status=data.get("status", "pending"),
            artifacts=data.get("artifacts", {}),
            region=data.get("region"),
            error_message=data.get("error_message"),
            stats=data.get("stats"),
            progress=TimestampProgressOut(**progress) if progress else None,
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            auto_scan_enabled=data.get("auto_scan_enabled", True),
            cancel_requested=data.get("cancel_requested", False),
        )

    @app.get("/videos/{video_id}/timestamp-region", response_model=TimestampRegionOut)
    def get_timestamp_region(
        video_id: str,
        project_id: Optional[str] = Query(None),
    ):
        pid = gap_map.resolve_project_id(video_id, project_id)
        if not pid:
            video = _resolve_video(video_id, project_id)
            pid = video["project_id"]
        doc = region_service.load_region(pid, video_id)
        if not doc or doc.get("method") == "deleted":
            raise HTTPException(404, "timestamp region not set")
        return TimestampRegionOut(**doc)

    @app.get("/videos/{video_id}/source-preview-frame")
    def source_preview_frame(
        video_id: str,
        project_id: Optional[str] = Query(None),
    ):
        """JPEG frame from source video — for region drawing before analysis keyframes exist."""
        video = _resolve_video(video_id, project_id)
        gap_map.write_video_index(
            video["project_id"],
            video_id,
            video["filename"],
            video.get("local_source_path"),
        )
        import cv2
        import tempfile
        from pathlib import Path

        try:
            bgr, _w, _h = region_service.capture_source_preview_bgr(
                video["project_id"],
                video_id,
                video["filename"],
                video.get("local_source_path"),
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            cv2.imwrite(tmp.name, bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
            path = tmp.name
        def _iter():
            try:
                with open(path, "rb") as fp:
                    yield from fp
            finally:
                Path(path).unlink(missing_ok=True)
        return StreamingResponse(_iter(), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})

    @app.put("/videos/{video_id}/timestamp-region", response_model=TimestampRegionOut)
    def put_timestamp_region(
        video_id: str,
        body: TimestampRegionIn,
        project_id: Optional[str] = Query(None),
    ):
        video = _resolve_video(video_id, project_id)
        gap_map.write_video_index(
            video["project_id"],
            video_id,
            video["filename"],
            video.get("local_source_path"),
        )
        try:
            doc = region_service.save_manual_region(
                video["project_id"],
                video_id,
                x=body.x,
                y=body.y,
                w=body.w,
                h=body.h,
                source_frame_index=body.source_frame_index,
                video_width=body.video_width,
                video_height=body.video_height,
                filename=video["filename"],
                local_source_path=video.get("local_source_path"),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return TimestampRegionOut(**doc)

    @app.delete("/videos/{video_id}/timestamp-region", status_code=204)
    def delete_timestamp_region(
        video_id: str,
        project_id: Optional[str] = Query(None),
    ):
        pid = gap_map.resolve_project_id(video_id, project_id)
        if not pid:
            video = _resolve_video(video_id, project_id)
            pid = video["project_id"]
        if not region_service.delete_region(pid, video_id):
            raise HTTPException(404, "no manual timestamp region to delete")

    @app.get("/videos/{video_id}/timestamp-region-preview")
    def timestamp_region_preview(
        video_id: str,
        project_id: Optional[str] = Query(None),
    ):
        """JPEG crop of the detected OSD region — the only pixels sent to OCR."""
        pid = gap_map.resolve_project_id(video_id, project_id)
        if not pid:
            video = _resolve_video(video_id, project_id)
            pid = video["project_id"]
        storage = get_storage()
        preview_key = key_timestamp_region_preview(pid, video_id)
        if not storage.exists(preview_key):
            raise HTTPException(404, "region preview not found — run timestamp-scan first")
        return StreamingResponse(
            storage.open_read(preview_key),
            media_type="image/jpeg",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/videos/{video_id}/timestamp-map", response_model=TimestampMapOut)
    def timestamp_map(
        video_id: str,
        project_id: Optional[str] = Query(None),
    ):
        video = _resolve_video(video_id, project_id)
        if video.get("status") != "analyzed":
            raise HTTPException(409, "video must be analyzed first")
        data = gap_map.load_gap_map(video["project_id"], video_id)
        if data is None:
            raise HTTPException(404, "timestamp map not found — run timestamp-scan first")
        return TimestampMapOut(
            region=data.get("region"),
            gaps=[GapIntervalOut(**g) for g in data.get("gaps", [])],
            stats=data.get("stats", {}),
            timeline_summary=data.get("timeline_summary"),
            timeline_viz=data.get("timeline_viz"),
            wall_clock_buckets=data.get("wall_clock_buckets"),
            hour_presence=data.get("hour_presence"),
            hour_coherence=data.get("hour_coherence"),
            ideal_day_hours=data.get("ideal_day_hours"),
            clock_hour_video_coverage=data.get("clock_hour_video_coverage"),
            ideal_day=data.get("ideal_day"),
            num_segments=data.get("num_segments"),
        )

    @app.post("/videos/{video_id}/timestamp-scan/stop")
    def stop_timestamp_scan(
        video_id: str,
        project_id: Optional[str] = Query(None),
    ):
        pid = gap_map.resolve_project_id(video_id, project_id)
        if not pid:
            video = _resolve_video(video_id, project_id)
            pid = video["project_id"]
        result = scan_control.stop_scan(pid, video_id)
        return {"stopped": True, "status": result}

    @app.delete("/videos/{video_id}/timestamp-scan")
    def delete_timestamp_scan(
        video_id: str,
        project_id: Optional[str] = Query(None),
    ):
        pid = gap_map.resolve_project_id(video_id, project_id)
        if not pid:
            video = _resolve_video(video_id, project_id)
            pid = video["project_id"]
        try:
            result = scan_control.delete_scan(pid, video_id)
        except scan_control.ScanInProgress as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"deleted": True, "status": result}

    @app.post("/videos/{video_id}/timestamp-scan")
    def timestamp_scan(
        video_id: str,
        body: TimestampScanRequest = TimestampScanRequest(),
        project_id: Optional[str] = Query(None),
    ):
        video = _resolve_video(video_id, project_id)
        if video.get("status") != "analyzed":
            raise HTTPException(409, "video must be analyzed first")

        gap_map.write_video_index(
            video["project_id"],
            video_id,
            video["filename"],
            video.get("local_source_path"),
        )

        storage = get_storage()
        status_key = key_timestamp_status(video["project_id"], video_id)
        if body.region:
            region_service.save_manual_region(
                video["project_id"],
                video_id,
                x=body.region.x,
                y=body.region.y,
                w=body.region.w,
                h=body.region.h,
                source_frame_index=body.region.source_frame_index,
                video_width=body.region.video_width,
                video_height=body.region.video_height,
                filename=video["filename"],
                local_source_path=video.get("local_source_path"),
            )

        prev_flags = storage.read_json(status_key) if storage.exists(status_key) else {}
        if not body.force and prev_flags:
            if prev_flags.get("status") == "done":
                return {"queued": False, "message": "already done", "status": prev_flags}
            if prev_flags.get("status") == "processing" and not prev_flags.get("cancel_requested"):
                return {"queued": False, "message": "already processing", "status": prev_flags}

        queued_at = datetime.now(timezone.utc).isoformat()
        storage.write_json(status_key, {
            "status": "processing",
            "cancel_requested": False,
            "auto_scan_enabled": prev_flags.get("auto_scan_enabled", True),
            "started_at": queued_at,
            "video_id": video_id,
            "project_id": video["project_id"],
            "progress": {
                "phase": "opening",
                "phase_label": "Opening video",
                "percent": 0,
                "message": "Queued",
            },
            "artifacts": gap_map._artifact_flags(video["project_id"], video_id),
        })

        from worker.pipeline import process_video

        region_override = None
        if body.region:
            region_override = {
                "x": body.region.x,
                "y": body.region.y,
                "w": body.region.w,
                "h": body.region.h,
                "method": "manual",
            }

        def _run():
            process_video(
                project_id=video["project_id"],
                video_id=video_id,
                filename=video["filename"],
                local_source_path=video.get("local_source_path"),
                region_override=region_override,
            )

        _executor.submit(_run)
        return {"queued": True, "video_id": video_id}

    @app.post("/videos/{video_id}/wall-clock-counts")
    def wall_clock_counts(
        video_id: str,
        body: CorrectedCountsRequest,
        project_id: Optional[str] = Query(None),
    ):
        video = _resolve_video(video_id, project_id)
        if video.get("status") != "analyzed":
            raise HTTPException(409, "video must be analyzed first")
        try:
            return count_sync.compute_wall_clock_bucket_counts(
                video["project_id"], video_id, body.line_ids
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/videos/{video_id}/corrected-counts", response_model=CorrectedCountsOut)
    def corrected_counts(
        video_id: str,
        body: CorrectedCountsRequest,
        project_id: Optional[str] = Query(None),
    ):
        video = _resolve_video(video_id, project_id)
        if video.get("status") != "analyzed":
            raise HTTPException(409, "video must be analyzed first")
        try:
            result = count_sync.compute_corrected_counts(
                video["project_id"], video_id, body.line_ids
            )
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise HTTPException(503, "counter API timed out — retry shortly") from exc
        except counter_client.CounterAPIError as exc:
            raise HTTPException(503, str(exc)) from exc
        return CorrectedCountsOut(
            gap_stats=result["gap_stats"],
            rows_excluded=result["rows_excluded"],
            rows_excluded_fraction=result["rows_excluded_fraction"],
            raw_counts=result["raw_counts"],
            corrected_counts=result["corrected_counts"],
            delta_per_line=[LineDeltaOut(**d) for d in result["delta_per_line"]],
        )

    return app


app = create_app()