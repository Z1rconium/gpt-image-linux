import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..app_state import app
from ..jobs import (
    get_generate_job_webhooks,
    queue_image_job,
    run_generate_job,
    serialize_sse_event,
    store_generate_job,
    trim_generate_jobs,
)
from ..sse_limiter import sse_limiter
from ...core import security as auth
from ...core import settings as config
from ...core.api_paths import normalize_api_path
from ...core.constants import ACTIVE_GENERATE_JOB_STATUSES, ERROR_GENERATE_JOB_STATUSES
from ...core.utils import utc_now
from ...repositories import storage
from ...schemas.models import (
    GenerateJobResponse,
    GenerateJobStatus,
    GenerateRequest,
    MessageResponse,
)


router = APIRouter()


def json_payload_key(payload: dict | list) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@router.post("/api/generate", response_model=GenerateJobResponse, status_code=202)
async def generate(req: GenerateRequest):
    def start_generate_job(
        job_id: str,
        api_url: str,
        api_key: str,
        api_path: str,
        api_preset_name: str,
        socks5_proxy: str,
    ):
        return run_generate_job(
            job_id,
            api_url,
            api_key,
            api_path,
            api_preset_name,
            req,
            socks5_proxy,
        )

    return queue_image_job(
        req=req,
        operation="generation",
        api_path=lambda preset: normalize_api_path(
            req.api_path or str(preset.get("api_path") or "/v1/images/generations")
        ),
        queued_message="Queued image generation",
        task_factory=start_generate_job,
    )


@router.get("/api/generate/jobs", response_model=list[GenerateJobStatus])
async def list_generate_jobs(
    include_finished: bool = Query(default=False),
    failed_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    if include_finished:
        statuses = ERROR_GENERATE_JOB_STATUSES if failed_only else None
        jobs = await asyncio.to_thread(storage.list_generate_jobs, statuses=statuses, limit=limit, offset=offset)
    else:
        jobs = await asyncio.to_thread(
            storage.list_generate_jobs,
            statuses=ACTIVE_GENERATE_JOB_STATUSES,
        )
    return [GenerateJobStatus(**job) for job in jobs]


@router.get("/api/generate/jobs/events")
async def stream_generate_jobs(request: Request):
    client_ip = auth.get_client_ip(request)
    if not await sse_limiter.acquire(client_ip):
        raise HTTPException(status_code=429, detail="Too many SSE connections")

    async def event_stream():
        start = time.monotonic()
        last_payload = None
        last_sent = 0.0
        try:
            while True:
                if await request.is_disconnected():
                    break
                if time.monotonic() - start > config.SSE_CONNECTION_TTL_SECONDS:
                    break
                jobs = await asyncio.to_thread(
                    storage.list_generate_jobs,
                    statuses=ACTIVE_GENERATE_JOB_STATUSES,
                )
                payload = json_payload_key(jobs)
                now = time.monotonic()
                if payload != last_payload:
                    last_payload = payload
                    last_sent = now
                    yield serialize_sse_event("jobs", jobs)
                elif now - last_sent >= 15:
                    last_sent = now
                    yield ": keep-alive\n\n"
                await asyncio.sleep(config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS)
        finally:
            await sse_limiter.release(client_ip)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/api/generate/jobs/history", response_model=MessageResponse)
async def clear_generate_job_history():
    deleted_count = await asyncio.to_thread(storage.clear_generate_job_history)
    return MessageResponse(
        status="success",
        message=f"Deleted {deleted_count} job history entr{'y' if deleted_count == 1 else 'ies'}",
    )


@router.get("/api/generate/{job_id}", response_model=GenerateJobStatus)
async def get_generate_job(job_id: str):
    job = app.state.generate_jobs.get(job_id) or await asyncio.to_thread(storage.get_generate_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return GenerateJobStatus(**job)


@router.get("/api/generate/{job_id}/events")
async def stream_generate_job(job_id: str, request: Request):
    job = app.state.generate_jobs.get(job_id) or await asyncio.to_thread(storage.get_generate_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")

    client_ip = auth.get_client_ip(request)
    if not await sse_limiter.acquire(client_ip):
        raise HTTPException(status_code=429, detail="Too many SSE connections")

    async def event_stream():
        start = time.monotonic()
        last_payload = None
        last_sent = 0.0
        try:
            while True:
                if await request.is_disconnected():
                    break
                if time.monotonic() - start > config.SSE_CONNECTION_TTL_SECONDS:
                    break
                current = await asyncio.to_thread(storage.get_generate_job, job_id)
                if not current:
                    break
                payload = json_payload_key(current)
                now = time.monotonic()
                if payload != last_payload:
                    last_payload = payload
                    last_sent = now
                    yield serialize_sse_event("job", current)
                    if current.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
                        break
                elif now - last_sent >= 15:
                    last_sent = now
                    yield ": keep-alive\n\n"
                await asyncio.sleep(config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS)
        finally:
            await sse_limiter.release(client_ip)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/api/generate/{job_id}", response_model=MessageResponse)
async def cancel_generate_job(job_id: str):
    job = app.state.generate_jobs.get(job_id) or await asyncio.to_thread(storage.get_generate_job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    if job.get("status") not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Generation job already finished")

    cancel_message = (
        "Image edit job cancelled"
        if job.get("operation") == "edit"
        else "Generation job cancelled"
    )
    store_generate_job(
        job_id,
        {
            "status": "cancelled",
            "stage": "cancelled",
            "message": cancel_message,
            "operation": job.get("operation"),
            "completed_at": utc_now(),
            "error": cancel_message,
        },
    )
    await asyncio.to_thread(storage.cancel_image_job_units, job_id)
    await asyncio.to_thread(trim_generate_jobs)

    get_generate_job_webhooks().pop(job_id, None)

    return MessageResponse(status="success", message="Generation job cancelled")
