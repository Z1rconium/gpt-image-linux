import asyncio
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from ..app_state import app
from ...services.job_events import (
    get_job_subscribers,
    get_jobs_subscribers,
    publish_queue,
    reconcile_active_generate_jobs,
    serialize_sse_event,
    store_generate_job,
)
from ...services.job_queue import (
    cleanup_parent_edit_sources,
    queue_image_job,
    trim_generate_jobs,
)
from ..sse_limiter import sse_limiter
from ...core import security as auth
from ...core import settings as config
from ...core.api_paths import normalize_api_path
from ...core.constants import ACTIVE_GENERATE_JOB_STATUSES, ERROR_GENERATE_JOB_STATUSES
from ...core.observability import metrics
from ...core.utils import utc_now
from ...repositories.image_jobs import (
    aggregate_image_job_units,
    cancel_image_job_units,
    clear_generate_job_history as clear_persisted_generate_job_history,
    get_generate_job as get_persisted_generate_job,
    get_generate_jobs_list_updated_at_edge,
    get_generate_jobs_updated_at_edges,
    list_generate_jobs as list_persisted_generate_jobs,
    release_edit_source_reservation,
)
from ...schemas.common import MessageResponse
from ...schemas.generation import (
    GenerateJobResponse,
    GenerateJobStatus,
    GenerateRequest,
)


router = APIRouter()
logger = logging.getLogger(__name__)
SSE_IDLE_CHECK_SECONDS = 1.0
SSE_QUEUE_MAXSIZE = 20


def json_payload_key(payload: dict | list) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _start_generate_jobs_sse_poller() -> None:
    task = getattr(app.state, "generate_jobs_sse_poller_task", None)
    if task and not task.done():
        return
    app.state.generate_jobs_sse_poller_task = asyncio.create_task(
        _poll_generate_jobs_sse()
    )


def _start_generate_job_sse_poller() -> None:
    task = getattr(app.state, "generate_job_sse_poller_task", None)
    if task and not task.done():
        return
    app.state.generate_job_sse_poller_task = asyncio.create_task(
        _poll_generate_job_sse()
    )


async def _poll_generate_jobs_sse() -> None:
    last_edge: tuple[int, str] | None = None
    try:
        while True:
            subscribers = list(get_jobs_subscribers())
            if not subscribers:
                break

            edge = await asyncio.to_thread(
                get_generate_jobs_list_updated_at_edge,
                statuses=ACTIVE_GENERATE_JOB_STATUSES,
            )
            metrics.increment("sse.poll_queries.generate_jobs")
            if edge != last_edge:
                last_edge = edge
                jobs = await asyncio.to_thread(
                    list_persisted_generate_jobs,
                    statuses=ACTIVE_GENERATE_JOB_STATUSES,
                )
                jobs = reconcile_active_generate_jobs(jobs)
                event = {"event": "jobs", "data": jobs}
                for queue in subscribers:
                    publish_queue(queue, event)

            await asyncio.sleep(config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Generate jobs SSE poller stopped after error", exc_info=True)
    finally:
        if getattr(app.state, "generate_jobs_sse_poller_task", None) is asyncio.current_task():
            app.state.generate_jobs_sse_poller_task = None


async def _poll_generate_job_sse() -> None:
    last_edges: dict[str, str] = {}
    try:
        while True:
            subscribers_by_job = {
                job_id: list(subscribers)
                for job_id, subscribers in get_job_subscribers().items()
                if subscribers
            }
            if not subscribers_by_job:
                break

            current_edges = await asyncio.to_thread(
                get_generate_jobs_updated_at_edges,
                job_ids=set(subscribers_by_job),
            )
            metrics.increment("sse.poll_queries.generate_job")
            for job_id in set(subscribers_by_job) - set(current_edges):
                for queue in subscribers_by_job[job_id]:
                    publish_queue(queue, {"event": "_missing", "data": None})
                last_edges.pop(job_id, None)

            changed_job_ids = [
                job_id
                for job_id, updated_at in current_edges.items()
                if last_edges.get(job_id) != updated_at
            ]
            for job_id in changed_job_ids:
                job = await asyncio.to_thread(get_persisted_generate_job, job_id)
                event = (
                    {"event": "job", "data": job}
                    if job
                    else {"event": "_missing", "data": None}
                )
                for queue in subscribers_by_job.get(job_id, []):
                    publish_queue(queue, event)
            last_edges = current_edges

            await asyncio.sleep(config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Generate job SSE poller stopped after error", exc_info=True)
    finally:
        if getattr(app.state, "generate_job_sse_poller_task", None) is asyncio.current_task():
            app.state.generate_job_sse_poller_task = None


@router.post("/api/generate", response_model=GenerateJobResponse, status_code=202)
async def generate(req: GenerateRequest):
    return queue_image_job(
        req=req,
        operation="generation",
        api_path=lambda preset: normalize_api_path(
            req.api_path or str(preset.get("api_path") or "/v1/images/generations")
        ),
        queued_message="Queued image generation",
    )


@router.get("/api/generate/jobs", response_model=list[GenerateJobStatus])
async def list_generate_jobs(
    include_finished: bool = Query(default=False),
    failed_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    before_updated_at: str | None = Query(default=None, max_length=64),
    before_job_id: str | None = Query(default=None, max_length=128),
):
    if include_finished:
        statuses = ERROR_GENERATE_JOB_STATUSES if failed_only else None
        jobs = await asyncio.to_thread(
            list_persisted_generate_jobs,
            statuses=statuses,
            limit=limit,
            offset=offset,
            before_updated_at=before_updated_at,
            before_job_id=before_job_id,
        )
    else:
        jobs = await asyncio.to_thread(
            list_persisted_generate_jobs,
            statuses=ACTIVE_GENERATE_JOB_STATUSES,
        )
    return [GenerateJobStatus(**job) for job in jobs]


@router.get("/api/generate/jobs/events")
async def stream_generate_jobs(request: Request):
    client_ip = auth.get_client_ip(request)
    sse_lease = await sse_limiter.acquire(client_ip)
    if not sse_lease:
        raise HTTPException(status_code=429, detail="Too many SSE connections")

    async def event_stream():
        start = time.monotonic()
        last_refresh_at = start
        last_payload = None
        last_sent = 0.0
        queue: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)
        subscribers = get_jobs_subscribers()
        subscribers.add(queue)
        _start_generate_jobs_sse_poller()
        try:
            jobs = await asyncio.to_thread(
                list_persisted_generate_jobs,
                statuses=ACTIVE_GENERATE_JOB_STATUSES,
            )
            jobs = reconcile_active_generate_jobs(jobs)
            last_payload = json_payload_key(jobs)
            last_sent = time.monotonic()
            yield serialize_sse_event("jobs", jobs)

            while True:
                if await request.is_disconnected():
                    break
                now = time.monotonic()
                refreshed_at = await sse_limiter.refresh_if_needed(
                    sse_lease,
                    last_refresh_at,
                )
                if refreshed_at is None:
                    break
                last_refresh_at = refreshed_at
                if now - start > config.SSE_CONNECTION_TTL_SECONDS:
                    break
                if now - last_sent >= 15:
                    last_sent = now
                    yield ": keep-alive\n\n"
                    continue
                wait_seconds = min(
                    SSE_IDLE_CHECK_SECONDS,
                    max(0.1, 15 - (now - last_sent)),
                    max(0.1, config.SSE_CONNECTION_TTL_SECONDS - (now - start)),
                )
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
                except asyncio.TimeoutError:
                    continue
                if event.get("event") != "jobs":
                    continue
                jobs = event.get("data") or []
                payload = json_payload_key(jobs)
                if payload == last_payload:
                    continue
                last_payload = payload
                last_sent = time.monotonic()
                yield serialize_sse_event("jobs", jobs)
        finally:
            subscribers.discard(queue)
            await sse_limiter.release(sse_lease)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/api/generate/jobs/history", response_model=MessageResponse)
async def clear_generate_job_history():
    deleted_count = await asyncio.to_thread(clear_persisted_generate_job_history)
    return MessageResponse(
        status="success",
        message=f"Deleted {deleted_count} job history entr{'y' if deleted_count == 1 else 'ies'}",
    )


@router.get("/api/generate/{job_id}", response_model=GenerateJobStatus)
async def get_generate_job(job_id: str):
    job = getattr(app.state, "generate_jobs", {}).get(job_id) or await asyncio.to_thread(
        get_persisted_generate_job,
        job_id,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return GenerateJobStatus(**job)


@router.get("/api/generate/{job_id}/events")
async def stream_generate_job(job_id: str, request: Request):
    job = getattr(app.state, "generate_jobs", {}).get(job_id) or await asyncio.to_thread(
        get_persisted_generate_job,
        job_id,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")

    client_ip = auth.get_client_ip(request)
    sse_lease = await sse_limiter.acquire(client_ip)
    if not sse_lease:
        raise HTTPException(status_code=429, detail="Too many SSE connections")

    async def event_stream():
        start = time.monotonic()
        last_refresh_at = start
        last_payload = None
        last_sent = 0.0
        queue: asyncio.Queue = asyncio.Queue(maxsize=SSE_QUEUE_MAXSIZE)
        subscribers = get_job_subscribers().setdefault(job_id, set())
        subscribers.add(queue)
        _start_generate_job_sse_poller()
        try:
            current = getattr(app.state, "generate_jobs", {}).get(job_id)
            if not current:
                current = await asyncio.to_thread(get_persisted_generate_job, job_id)
            if not current:
                return
            last_payload = json_payload_key(current)
            last_sent = time.monotonic()
            yield serialize_sse_event("job", current)
            if current.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
                return

            while True:
                if await request.is_disconnected():
                    break
                now = time.monotonic()
                refreshed_at = await sse_limiter.refresh_if_needed(
                    sse_lease,
                    last_refresh_at,
                )
                if refreshed_at is None:
                    break
                last_refresh_at = refreshed_at
                if now - start > config.SSE_CONNECTION_TTL_SECONDS:
                    break
                if now - last_sent >= 15:
                    last_sent = now
                    yield ": keep-alive\n\n"
                    continue
                wait_seconds = min(
                    SSE_IDLE_CHECK_SECONDS,
                    max(0.1, 15 - (now - last_sent)),
                    max(0.1, config.SSE_CONNECTION_TTL_SECONDS - (now - start)),
                )
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=wait_seconds)
                except asyncio.TimeoutError:
                    continue
                if event.get("event") == "_missing":
                    break
                if event.get("event") != "job":
                    continue
                current = event.get("data")
                if not current:
                    break
                payload = json_payload_key(current)
                if payload == last_payload:
                    continue
                last_payload = payload
                last_sent = time.monotonic()
                yield serialize_sse_event("job", current)
                if current.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
                    break
        finally:
            subscribers.discard(queue)
            if not subscribers:
                get_job_subscribers().pop(job_id, None)
            await sse_limiter.release(sse_lease)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "private, no-store",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/api/generate/{job_id}", response_model=MessageResponse)
async def cancel_generate_job(job_id: str):
    job = getattr(app.state, "generate_jobs", {}).get(job_id) or await asyncio.to_thread(
        get_persisted_generate_job,
        job_id,
    )
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    if job.get("status") not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Generation job already finished")

    aggregate = await asyncio.to_thread(aggregate_image_job_units, job_id)
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
    await asyncio.to_thread(cancel_image_job_units, job_id)
    await asyncio.to_thread(trim_generate_jobs)

    if job.get("operation") == "edit":
        if int(aggregate.get("running_count") or 0) > 0:
            await asyncio.to_thread(release_edit_source_reservation, job_id)
        else:
            await asyncio.to_thread(cleanup_parent_edit_sources, job_id)

    return MessageResponse(status="success", message="Generation job cancelled")
