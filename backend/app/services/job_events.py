"""Generation job state, persistence, and SSE publication."""

import asyncio
import json
import time

from fastapi import HTTPException

from ..api.app_state import (
    GENERATE_JOB_PERSIST_INTERVAL_SECONDS,
    GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS,
    app,
)
from ..core import settings as config
from ..core import validators as ssrf
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.utils import utc_now
from ..repositories.image_jobs import (
    get_generate_job,
    list_generate_jobs,
    pop_generate_job_webhook,
    upsert_generate_job,
)
from . import webhook_service as webhooks
from .blocking import run_db_operation

def get_job_subscribers() -> dict[str, set[asyncio.Queue]]:
    subscribers = getattr(app.state, "generate_job_subscribers", None)
    if not isinstance(subscribers, dict):
        subscribers = {}
        app.state.generate_job_subscribers = subscribers
    return subscribers


def get_jobs_subscribers() -> set[asyncio.Queue]:
    subscribers = getattr(app.state, "generate_jobs_subscribers", None)
    if not isinstance(subscribers, set):
        subscribers = set()
        app.state.generate_jobs_subscribers = subscribers
    return subscribers


def serialize_sse_event(event: str, data: dict | list) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def publish_queue(queue: asyncio.Queue, event: dict):
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


def publish_generate_job(
    job: dict,
    *,
    list_debounce: bool = True,
    list_reconcile: bool = False,
):
    event = {"event": "job", "data": public_generate_job(job)}
    for queue in list(get_job_subscribers().get(job["job_id"], set())):
        publish_queue(queue, event)
    publish_generate_jobs(debounce=list_debounce, reconcile=list_reconcile)


def sort_generate_jobs(jobs: list[dict]) -> list[dict]:
    jobs.sort(
        key=lambda job: job.get("updated_at") or job.get("created_at", ""),
        reverse=True,
    )
    return jobs


def snapshot_active_generate_jobs_from_memory() -> list[dict]:
    generate_jobs = getattr(app.state, "generate_jobs", {})
    jobs = [
        public_generate_job(job)
        for job in generate_jobs.values()
        if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES
    ]
    return sort_generate_jobs(jobs)


def reconcile_active_generate_jobs(storage_jobs: list[dict]) -> list[dict]:
    jobs_by_id = {job["job_id"]: job for job in storage_jobs}
    local_jobs = getattr(app.state, "generate_jobs", {})
    for job_id, job in local_jobs.items():
        if job.get("status") not in ACTIVE_GENERATE_JOB_STATUSES:
            continue
        if job_id not in jobs_by_id:
            continue
        storage_job = jobs_by_id[job_id]
        if str(job.get("updated_at") or "") >= str(storage_job.get("updated_at") or ""):
            jobs_by_id[job_id] = job
    app.state.generate_jobs = jobs_by_id
    return snapshot_active_generate_jobs_from_memory()


def reconcile_active_generate_jobs_from_storage() -> list[dict]:
    return reconcile_active_generate_jobs(
        list_generate_jobs(statuses=ACTIVE_GENERATE_JOB_STATUSES)
    )


def list_active_generate_jobs(*, reconcile: bool = False) -> list[dict]:
    if reconcile:
        return reconcile_active_generate_jobs_from_storage()
    return snapshot_active_generate_jobs_from_memory()


def publish_generate_jobs_now(*, reconcile: bool = False):
    jobs = list_active_generate_jobs(reconcile=reconcile)
    event = {"event": "jobs", "data": jobs}
    for queue in list(get_jobs_subscribers()):
        publish_queue(queue, event)


async def publish_generate_jobs_debounced():
    try:
        await asyncio.sleep(GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS)
        reconcile = bool(app.state.generate_jobs_broadcast_reconcile)
        app.state.generate_jobs_broadcast_reconcile = False
        publish_generate_jobs_now(reconcile=reconcile)
    finally:
        if app.state.generate_jobs_broadcast_task is asyncio.current_task():
            app.state.generate_jobs_broadcast_task = None


def cancel_pending_generate_jobs_broadcast():
    task = app.state.generate_jobs_broadcast_task
    if task and not task.done():
        task.cancel()
    app.state.generate_jobs_broadcast_task = None
    app.state.generate_jobs_broadcast_reconcile = False


def publish_generate_jobs(*, debounce: bool = True, reconcile: bool = False):
    if not get_jobs_subscribers():
        return

    if not debounce:
        cancel_pending_generate_jobs_broadcast()
        publish_generate_jobs_now(reconcile=reconcile)
        return

    if reconcile:
        app.state.generate_jobs_broadcast_reconcile = True

    task = app.state.generate_jobs_broadcast_task
    if task and not task.done():
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        publish_generate_jobs_now(reconcile=reconcile)
        return

    app.state.generate_jobs_broadcast_task = loop.create_task(
        publish_generate_jobs_debounced()
    )


def public_generate_job(job: dict) -> dict:
    result = job.copy()
    result.pop("webhook_url", None)
    return result


def validate_job_webhook_url(webhook_url: str | None) -> str | None:
    normalized_url = str(webhook_url or "").strip()
    if not normalized_url:
        return None
    try:
        ssrf.validate_webhook_url(normalized_url, config.WEBHOOK_HOST_ALLOWLIST)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if len(config.WEBHOOK_SIGNING_SECRET.encode("utf-8")) < 32:
        raise HTTPException(
            status_code=422,
            detail="WEBHOOK_SIGNING_SECRET must contain at least 32 bytes",
        )
    return normalized_url


def dispatch_job_webhook(job: dict):
    webhook_url = pop_generate_job_webhook(job["job_id"])
    if not webhook_url:
        return
    asyncio.create_task(webhooks.deliver_webhook(webhook_url, public_generate_job(job)))


async def dispatch_job_webhook_async(job: dict):
    webhook_url = await run_db_operation(
        pop_generate_job_webhook,
        job["job_id"],
        metric_name="pop_generate_job_webhook",
    )
    if webhook_url:
        asyncio.create_task(
            webhooks.deliver_webhook(webhook_url, public_generate_job(job))
        )


def build_job_update(job_id: str, updates: dict) -> dict:
    now = utc_now()
    existing = app.state.generate_jobs.get(job_id) or get_generate_job(job_id) or {}
    job = {
        **existing,
        **updates,
        "job_id": job_id,
        "updated_at": now,
    }
    if "created_at" not in job:
        job["created_at"] = now
    if job.get("image_id"):
        job["id"] = job["image_id"]
    return job


def should_persist_generate_job(job_id: str, job: dict, persist: bool) -> bool:
    if persist:
        return True
    if job.get("status") != "running":
        return True

    last_persist_at = app.state.generate_job_last_persist_at
    now = time.monotonic()
    previous = last_persist_at.get(job_id)
    if previous is None or now - previous >= GENERATE_JOB_PERSIST_INTERVAL_SECONDS:
        last_persist_at[job_id] = now
        return True
    return False


def store_generate_job(job_id: str, updates: dict, *, persist: bool = True) -> dict:
    job = build_job_update(job_id, updates)
    status = job.get("status")
    if status in ACTIVE_GENERATE_JOB_STATUSES:
        app.state.generate_jobs[job_id] = job
    else:
        app.state.generate_jobs.pop(job_id, None)
        app.state.generate_job_last_persist_at.pop(job_id, None)
    if should_persist_generate_job(job_id, job, persist):
        upsert_generate_job(job)
    is_terminal = status not in ACTIVE_GENERATE_JOB_STATUSES
    publish_generate_job(
        job,
        list_debounce=not is_terminal,
        list_reconcile=False,
    )
    if is_terminal:
        dispatch_job_webhook(job)
    return job


async def store_generate_job_async(
    job_id: str,
    updates: dict,
    *,
    persist: bool = True,
) -> dict:
    existing = app.state.generate_jobs.get(job_id)
    if existing is None:
        existing = await run_db_operation(
            get_generate_job,
            job_id,
            metric_name="get_generate_job_for_update",
        ) or {}
    now = utc_now()
    job = {**existing, **updates, "job_id": job_id, "updated_at": now}
    if "created_at" not in job:
        job["created_at"] = now
    if job.get("image_id"):
        job["id"] = job["image_id"]

    status = job.get("status")
    if status in ACTIVE_GENERATE_JOB_STATUSES:
        app.state.generate_jobs[job_id] = job
    else:
        app.state.generate_jobs.pop(job_id, None)
        app.state.generate_job_last_persist_at.pop(job_id, None)

    if should_persist_generate_job(job_id, job, persist):
        await run_db_operation(
            upsert_generate_job,
            job,
            metric_name="persist_generate_job",
        )

    is_terminal = status not in ACTIVE_GENERATE_JOB_STATUSES
    publish_generate_job(
        job,
        list_debounce=not is_terminal,
        list_reconcile=False,
    )
    if is_terminal:
        await dispatch_job_webhook_async(job)
    return job

