import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..app_state import app
from ..jobs import (
    get_generate_job_tasks,
    get_generate_job_webhooks,
    get_job_subscribers,
    get_jobs_subscribers,
    list_active_generate_jobs,
    publish_queue,
    queue_image_job,
    run_generate_job,
    serialize_sse_event,
    store_generate_job,
    trim_generate_jobs,
)
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
        jobs = list_active_generate_jobs()
    return [GenerateJobStatus(**job) for job in jobs]


@router.get("/api/generate/jobs/events")
async def stream_generate_jobs(request: Request):
    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    subscribers = get_jobs_subscribers()
    subscribers.add(queue)
    active_jobs = list_active_generate_jobs(reconcile=True)
    publish_queue(
        queue,
        {"event": "jobs", "data": active_jobs},
    )

    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield serialize_sse_event(item["event"], item["data"])
        finally:
            subscribers.discard(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
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

    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    subscribers_by_job = get_job_subscribers()
    subscribers = subscribers_by_job.setdefault(job_id, set())
    subscribers.add(queue)
    publish_queue(queue, {"event": "job", "data": job})

    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                data = item["data"]
                yield serialize_sse_event(item["event"], data)
                if data.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
                    break
        finally:
            subscribers.discard(queue)
            if not subscribers:
                subscribers_by_job.pop(job_id, None)

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
    await asyncio.to_thread(trim_generate_jobs)

    get_generate_job_webhooks().pop(job_id, None)
    task = get_generate_job_tasks().pop(job_id, None)
    if task and not task.done():
        task.cancel()

    return MessageResponse(status="success", message="Generation job cancelled")
