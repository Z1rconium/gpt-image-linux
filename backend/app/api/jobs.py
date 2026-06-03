import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import HTTPException

from .app_state import (
    GENERATE_JOB_PERSIST_INTERVAL_SECONDS,
    GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS,
    MAX_GENERATE_JOBS,
    app,
)
from .presets import (
    get_active_preset,
    get_api_presets,
    get_effective_preset_api_key,
    get_exception_message,
    get_upstream_socks5_proxy,
    get_webhook_url,
)
from ..core import settings as config
from ..core.api_paths import normalize_default_model
from ..core.observability import JobStageTimer, metrics, use_job_stage_timer
from ..core import validators as ssrf
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.utils import beijing_now, utc_now
from ..integrations import upstream_client as proxy
from ..repositories import storage
from ..schemas.models import EditRequest, GenerateRequest, GenerateJobResponse, GalleryEntry
from ..services import webhook_service as webhooks


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EditImageSource:
    temp_path: Path
    byte_size: int
    filename: str
    content_type: str


@dataclass(frozen=True)
class ImageJobOutcome:
    entries: list[GalleryEntry]
    success_message: str | None = None
    error_message: str | None = None


def get_job_subscribers() -> dict[str, set[asyncio.Queue]]:
    return app.state.generate_job_subscribers


def get_jobs_subscribers() -> set[asyncio.Queue]:
    return app.state.generate_jobs_subscribers


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
    event = {"event": "job", "data": job}
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
    jobs = [
        job.copy()
        for job in app.state.generate_jobs.values()
        if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES
    ]
    return sort_generate_jobs(jobs)


def reconcile_active_generate_jobs_from_storage() -> list[dict]:
    jobs_by_id = {
        job["job_id"]: job
        for job in storage.list_generate_jobs(statuses=ACTIVE_GENERATE_JOB_STATUSES)
    }
    for job_id, job in app.state.generate_jobs.items():
        if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES:
            jobs_by_id[job_id] = job
    app.state.generate_jobs = jobs_by_id
    return snapshot_active_generate_jobs_from_memory()


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


def get_generate_job_webhooks() -> dict[str, str]:
    return app.state.generate_job_webhooks


def validate_job_webhook_url(webhook_url: str | None) -> str | None:
    normalized_url = str(webhook_url or "").strip()
    if not normalized_url:
        return None
    try:
        ssrf.validate_webhook_url(normalized_url, config.WEBHOOK_HOST_ALLOWLIST)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not config.WEBHOOK_SIGNING_SECRET:
        raise HTTPException(
            status_code=422,
            detail="WEBHOOK_SIGNING_SECRET is required to sign webhook callbacks",
        )
    return normalized_url


def dispatch_job_webhook(job: dict):
    webhook_url = get_generate_job_webhooks().pop(job["job_id"], "")
    if not webhook_url:
        return
    asyncio.create_task(webhooks.deliver_webhook(webhook_url, job.copy()))


def build_job_update(job_id: str, updates: dict) -> dict:
    now = utc_now()
    existing = app.state.generate_jobs.get(job_id) or storage.get_generate_job(job_id) or {}
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
        storage.upsert_generate_job(job)
    is_terminal = status not in ACTIVE_GENERATE_JOB_STATUSES
    publish_generate_job(
        job,
        list_debounce=not is_terminal,
        list_reconcile=False,
    )
    if is_terminal:
        dispatch_job_webhook(job)
    return job


def trim_generate_jobs():
    storage.trim_generate_jobs(MAX_GENERATE_JOBS)


def get_generate_job_tasks() -> dict[str, asyncio.Task]:
    return app.state.generate_job_tasks


def get_generate_job_semaphore() -> asyncio.Semaphore:
    semaphore = getattr(app.state, "generate_job_semaphore", None)
    if semaphore is None:
        semaphore = asyncio.Semaphore(config.MAX_ACTIVE_GENERATE_JOBS)
        app.state.generate_job_semaphore = semaphore
    return semaphore


def get_upstream_request_semaphore() -> asyncio.Semaphore:
    semaphore = getattr(app.state, "upstream_request_semaphore", None)
    if semaphore is None:
        semaphore = asyncio.Semaphore(config.MAX_ACTIVE_GENERATE_JOBS)
        app.state.upstream_request_semaphore = semaphore
    return semaphore


def get_pending_edit_source_bytes() -> int:
    return max(0, int(getattr(app.state, "pending_edit_source_bytes", 0) or 0))


def get_max_pending_edit_source_bytes() -> int:
    return max(0, config.MAX_PENDING_EDIT_SOURCE_MB) * 1024 * 1024


def reserve_pending_edit_source_bytes(byte_count: int):
    if byte_count <= 0:
        return
    app.state.pending_edit_source_bytes = get_pending_edit_source_bytes() + byte_count


def release_pending_edit_source_bytes(byte_count: int):
    if byte_count <= 0:
        return
    app.state.pending_edit_source_bytes = max(
        0,
        get_pending_edit_source_bytes() - byte_count,
    )


def count_active_jobs() -> int:
    jobs = app.state.generate_jobs or {}
    return sum(
        1
        for job in jobs.values()
        if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES
    )


def request_image_units(req: GenerateRequest | EditRequest) -> int:
    try:
        return max(1, int(req.n or 1))
    except (TypeError, ValueError):
        return 1


def job_image_units(job: dict) -> int:
    try:
        return max(1, int(job.get("image_units") or job.get("n") or 1))
    except (TypeError, ValueError):
        return 1


def count_active_job_units() -> int:
    return storage.count_active_image_job_units()


def snapshot_queue_metrics() -> dict[str, int]:
    jobs = app.state.generate_jobs or {}
    running_units, queued_units = storage.count_pending_image_job_units()
    counts: dict[str, int] = {
        "image_jobs.active": 0,
        "image_jobs.active_units": running_units + queued_units,
        "image_jobs.queued": queued_units,
        "image_jobs.running": running_units,
        "image_jobs.capacity": config.MAX_ACTIVE_GENERATE_JOBS + config.MAX_QUEUED_GENERATE_JOBS,
        "image_jobs.running_capacity": config.MAX_ACTIVE_GENERATE_JOBS,
        "image_jobs.queued_capacity": config.MAX_QUEUED_GENERATE_JOBS,
        "image_jobs.upstream_request_capacity": config.MAX_ACTIVE_GENERATE_JOBS,
        "image_jobs.tasks": len(get_generate_job_tasks()),
        "image_jobs.sse_job_subscribers": sum(
            len(subscribers)
            for subscribers in get_job_subscribers().values()
        ),
        "image_jobs.sse_jobs_subscribers": len(get_jobs_subscribers()),
        "edit_sources.pending_bytes": get_pending_edit_source_bytes(),
        "edit_sources.pending_capacity_bytes": get_max_pending_edit_source_bytes(),
    }
    for operation in ("generation", "edit"):
        for status in ("queued", "running"):
            counts[f"image_jobs.{operation}.{status}.current"] = 0

    for job in jobs.values():
        status = str(job.get("status") or "")
        if status not in ACTIVE_GENERATE_JOB_STATUSES:
            continue
        operation = str(job.get("operation") or "generation")
        counts["image_jobs.active"] += 1
        if operation in {"generation", "edit"}:
            counts[f"image_jobs.{operation}.{status}.current"] += 1

    return counts


def ensure_job_queue_capacity(
    extra_pending_edit_source_bytes: int = 0,
    image_units: int = 1,
):
    capacity = config.MAX_ACTIVE_GENERATE_JOBS + config.MAX_QUEUED_GENERATE_JOBS
    requested_units = max(1, int(image_units or 1))
    running_units, queued_units = storage.count_pending_image_job_units()
    if running_units + queued_units + requested_units > capacity:
        metrics.increment("image_jobs.rejected.queue_full")
        raise HTTPException(status_code=429, detail="Generation job queue is full")
    max_pending_edit_source_bytes = get_max_pending_edit_source_bytes()
    if (
        extra_pending_edit_source_bytes > 0
        and max_pending_edit_source_bytes > 0
        and get_pending_edit_source_bytes() + extra_pending_edit_source_bytes
        > max_pending_edit_source_bytes
    ):
        metrics.increment("image_jobs.rejected.edit_source_full")
        raise HTTPException(status_code=429, detail="Edit source queue is full")


def track_generate_job_task(job_id: str, task: asyncio.Task):
    tasks = get_generate_job_tasks()
    tasks[job_id] = task
    task.add_done_callback(
        lambda _task, tracked_job_id=job_id: get_generate_job_tasks().pop(
            tracked_job_id,
            None,
        )
    )


def build_pending_job(
    job_id: str,
    req: GenerateRequest | EditRequest,
    operation: str,
    message: str,
    api_path: str | None = None,
    api_preset_name: str | None = None,
    image_units: int = 1,
) -> dict:
    now = utc_now()
    return {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "message": message,
        "operation": operation,
        "prompt": req.prompt,
        "size": req.size,
        "created_at": now,
        "updated_at": now,
        "model": req.model,
        "quality": req.quality,
        "output_format": req.output_format,
        "output_compression": req.output_compression,
        "response_format": req.response_format,
        "n": req.n,
        "image_units": max(1, int(image_units or 1)),
        "api_path": api_path,
        "api_preset_name": api_preset_name,
    }


def gallery_entry_job_image(entry: GalleryEntry) -> dict:
    return {
        "image_id": entry.id,
        "image_url": f"/api/image/{entry.filename}",
        "filename": entry.filename,
        "image_width": entry.image_width,
        "image_height": entry.image_height,
    }


def gallery_entry_job_result(entry: GalleryEntry) -> dict:
    image = gallery_entry_job_image(entry)
    image["prompt"] = entry.prompt
    image["size"] = entry.size
    image["model"] = entry.model
    image["quality"] = entry.quality
    image["output_format"] = entry.output_format
    image["output_compression"] = entry.output_compression
    image["response_format"] = entry.response_format
    image["api_path"] = entry.api_path
    image["api_preset_name"] = entry.api_preset_name
    image["completed_at"] = entry.completed_at
    return image


def edit_source_to_payload(source: EditImageSource) -> dict:
    return {
        "temp_path": str(source.temp_path),
        "byte_size": source.byte_size,
        "filename": source.filename,
        "content_type": source.content_type,
    }


def edit_source_from_payload(payload: dict) -> EditImageSource:
    return EditImageSource(
        temp_path=Path(str(payload.get("temp_path") or "")),
        byte_size=int(payload.get("byte_size") or 0),
        filename=str(payload.get("filename") or "image.png"),
        content_type=str(payload.get("content_type") or "application/octet-stream"),
    )


def build_request_payload(req: GenerateRequest | EditRequest) -> dict:
    data = req.model_dump(mode="json")
    data.pop("webhook_url", None)
    return data


def rebuild_request(operation: str, payload: dict) -> GenerateRequest | EditRequest:
    request_data = {**payload, "n": 1}
    if operation == "edit":
        return EditRequest(**request_data)
    return GenerateRequest(**request_data)


def get_preset_for_unit(unit: dict) -> dict | None:
    preset_id = str(unit.get("api_preset_id") or "")
    preset_name = str(unit.get("api_preset_name") or "")
    for preset in get_api_presets():
        if preset_id and preset.get("id") == preset_id:
            return preset
    for preset in get_api_presets():
        if preset_name and preset.get("name") == preset_name:
            return preset
    return get_active_preset()


def cleanup_parent_edit_sources(parent_job_id: str):
    aggregate = storage.aggregate_image_job_units(parent_job_id)
    paths: set[str] = set()
    total_bytes = 0
    for unit in aggregate.get("units", []):
        for source in unit.get("edit_sources") or []:
            path = str(source.get("temp_path") or "")
            if path:
                paths.add(path)
            try:
                total_bytes += int(source.get("byte_size") or 0)
            except (TypeError, ValueError):
                pass
    for path in paths:
        Path(path).unlink(missing_ok=True)
    release_pending_edit_source_bytes(total_bytes)


def summarize_unit_failures(failures: list[dict], total: int, operation: str) -> str:
    if total == 1 and failures:
        return str(failures[0].get("error") or failures[0].get("message") or "failed")
    sample_messages = []
    for unit in failures[:3]:
        index = int(unit.get("unit_index") or 0)
        message = str(unit.get("error") or unit.get("message") or "failed")
        sample_messages.append(f"#{index + 1}: {message}")
    if len(failures) > len(sample_messages):
        sample_messages.append(f"... and {len(failures) - len(sample_messages)} more")
    suffix = "; ".join(sample_messages) if sample_messages else "no image data"
    noun = "image generation requests" if operation == "generation" else "image edit requests"
    return f"{len(failures)} of {total} {noun} failed: {suffix}"


def aggregate_parent_image_job(parent_job_id: str, *, force_publish: bool = False) -> dict | None:
    parent = storage.get_generate_job(parent_job_id)
    if not parent:
        return None
    aggregate = storage.aggregate_image_job_units(parent_job_id)
    total = int(aggregate.get("total") or parent.get("n") or 1)
    completed = int(aggregate.get("completed") or 0)
    success_count = int(aggregate.get("success_count") or 0)
    running_count = int(aggregate.get("running_count") or 0)
    queued_count = int(aggregate.get("queued_count") or 0)
    operation = str(parent.get("operation") or "generation")

    if aggregate.get("all_terminal"):
        images = aggregate.get("images") or []
        failures = aggregate.get("failures") or []
        first_image = images[0] if images else {}
        completed_at = str(first_image.get("completed_at") or beijing_now())
        if images:
            message = (
                "Image edit completed"
                if operation == "edit"
                else "Image generation completed"
            )
            if failures:
                message = f"Generated {len(images)} of {total} requested images; {len(failures)} failed"
            update = {
                "status": "success",
                "stage": "completed",
                "message": message,
                "operation": operation,
                "image_id": first_image.get("image_id"),
                "image_url": first_image.get("image_url"),
                "images": images,
                "prompt": parent.get("prompt"),
                "size": parent.get("size"),
                "image_width": first_image.get("image_width"),
                "image_height": first_image.get("image_height"),
                "model": parent.get("model"),
                "quality": parent.get("quality"),
                "output_format": parent.get("output_format"),
                "output_compression": parent.get("output_compression"),
                "response_format": parent.get("response_format"),
                "n": parent.get("n"),
                "api_path": parent.get("api_path"),
                "api_preset_name": parent.get("api_preset_name"),
                "stage_timings": aggregate.get("stage_timings") or {},
                "completed_at": completed_at,
            }
            if failures:
                update["error"] = summarize_unit_failures(failures, total, operation)
        elif aggregate.get("all_cancelled"):
            cancel_message = (
                "Image edit job cancelled"
                if operation == "edit"
                else "Generation job cancelled"
            )
            update = {
                "status": "cancelled",
                "stage": "cancelled",
                "message": cancel_message,
                "operation": operation,
                "completed_at": completed_at,
                "error": cancel_message,
            }
        else:
            failures = failures or aggregate.get("units") or []
            status = (
                "upstream_error"
                if failures and all(unit.get("status") == "upstream_error" for unit in failures)
                else "error"
            )
            error_message = summarize_unit_failures(failures, total, operation)
            update = {
                "status": status,
                "stage": "generation_failed" if operation == "generation" else "edit_failed",
                "message": error_message,
                "operation": operation,
                "completed_at": completed_at,
                "error": error_message,
                "stage_timings": aggregate.get("stage_timings") or {},
            }
        job = store_generate_job(parent_job_id, update)
        trim_generate_jobs()
        if operation == "edit":
            cleanup_parent_edit_sources(parent_job_id)
        return job

    if running_count > 0 or success_count > 0:
        stage = "waiting_for_api"
        message = (
            f"Editing images ({success_count}/{total} completed)"
            if operation == "edit"
            else f"Generating images ({success_count}/{total} completed)"
        )
        return store_generate_job(
            parent_job_id,
            {
                "status": "running",
                "stage": stage,
                "message": message,
                "operation": operation,
                "started_at": parent.get("started_at") or utc_now(),
            },
            persist=force_publish,
        )

    if queued_count > 0:
        return store_generate_job(
            parent_job_id,
            {
                "status": "queued",
                "stage": "queued",
                "message": parent.get("message") or "Queued image generation",
                "operation": operation,
            },
            persist=force_publish,
        )
    return parent


def set_generate_job_progress(
    job_id: str,
    stage: str,
    message: str,
    operation: str,
):
    job = app.state.generate_jobs.get(job_id)
    if not job:
        return

    store_generate_job(
        job_id,
        {
            "status": "running",
            "stage": stage,
            "message": message,
            "operation": operation,
        },
        persist=False,
    )


def build_edit_request_from_form(
    prompt: str,
    size: str,
    model: str,
    n: int,
    quality: str,
    output_format: str,
    output_compression: int | None,
    response_format: str | None,
    webhook_url: str | None,
) -> EditRequest:
    try:
        return EditRequest(
            prompt=prompt,
            size=size,
            model=model,
            n=n,
            quality=quality,
            output_format=output_format,
            output_compression=output_compression,
            response_format=response_format,
            webhook_url=webhook_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


def queue_image_job(
    *,
    req: GenerateRequest | EditRequest,
    operation: Literal["generation", "edit"],
    api_path: str | Callable[[dict], str],
    queued_message: str,
    task_factory: Callable[[str, str, str, str, str, str], Awaitable[None]],
    pending_edit_source_bytes: int = 0,
    edit_sources_payload: list[dict] | None = None,
) -> GenerateJobResponse:
    active_preset = get_active_preset()
    active_preset_id = str(active_preset.get("id") or "default")
    api_url = str(active_preset.get("api_url") or "").rstrip("/")
    api_preset_name = active_preset.get("name") or "Untitled preset"
    resolved_api_path = api_path(active_preset) if callable(api_path) else api_path
    requested_model = (
        str(req.model or "").strip()
        if "model" in getattr(req, "model_fields_set", set())
        else ""
    )
    req.model = requested_model or normalize_default_model(
        active_preset.get("default_model"),
        resolved_api_path,
    )

    if not api_url:
        raise HTTPException(
            status_code=400,
            detail="API URL not configured. Please set it in Settings.",
        )
    try:
        api_url = ssrf.normalize_upstream_base_url(api_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    api_key = get_effective_preset_api_key(active_preset)
    socks5_proxy = get_upstream_socks5_proxy()

    webhook_url = validate_job_webhook_url(req.webhook_url or get_webhook_url())
    image_units = request_image_units(req)
    ensure_job_queue_capacity(
        pending_edit_source_bytes,
        image_units=image_units,
    )
    job_id = str(uuid.uuid4())
    reserved_edit_source_bytes = 0
    try:
        if pending_edit_source_bytes > 0:
            reserve_pending_edit_source_bytes(pending_edit_source_bytes)
            reserved_edit_source_bytes = pending_edit_source_bytes
        if webhook_url:
            get_generate_job_webhooks()[job_id] = webhook_url
        pending_job = build_pending_job(
            job_id=job_id,
            req=req,
            operation=operation,
            message=queued_message,
            api_path=resolved_api_path,
            api_preset_name=api_preset_name,
            image_units=image_units,
        )
        store_generate_job(job_id, pending_job)
        storage.create_image_job_units(
            parent_job_id=job_id,
            operation=operation,
            request=build_request_payload(req),
            image_units=image_units,
            api_preset_id=active_preset_id,
            api_preset_name=api_preset_name,
            api_path=resolved_api_path,
            edit_sources=edit_sources_payload,
        )
        metrics.increment(f"image_jobs.{operation}.queued")
        publish_generate_jobs(debounce=False, reconcile=True)
    except BaseException:
        release_pending_edit_source_bytes(reserved_edit_source_bytes)
        get_generate_job_webhooks().pop(job_id, None)
        app.state.generate_jobs.pop(job_id, None)
        app.state.generate_job_last_persist_at.pop(job_id, None)
        raise

    return GenerateJobResponse(
        job_id=job_id,
        status="queued",
        stage="queued",
        message=queued_message,
        operation=operation,
    )


def queue_edit_job(
    req: EditRequest,
    image_sources: list[EditImageSource],
) -> GenerateJobResponse:
    image_source_bytes = sum(source.byte_size for source in image_sources)

    def start_edit_job(
        job_id: str,
        api_url: str,
        api_key: str,
        _api_path: str,
        api_preset_name: str,
        socks5_proxy: str,
    ) -> Awaitable[None]:
        return run_edit_job(
            job_id,
            api_url,
            api_key,
            api_preset_name,
            req,
            image_sources,
            image_source_bytes,
            socks5_proxy,
        )

    return queue_image_job(
        req=req,
        operation="edit",
        api_path="/v1/images/edits",
        queued_message="Queued image edit",
        task_factory=start_edit_job,
        pending_edit_source_bytes=image_source_bytes,
        edit_sources_payload=[edit_source_to_payload(source) for source in image_sources],
    )


def normalize_image_job_outcome(
    result: list[GalleryEntry] | ImageJobOutcome,
) -> ImageJobOutcome:
    if isinstance(result, ImageJobOutcome):
        return result
    return ImageJobOutcome(entries=result)


def summarize_batch_generation_failures(
    failures: list[tuple[int, BaseException]],
    total: int,
) -> str:
    sample_messages = []
    for index, error in failures[:3]:
        message = str(error) or repr(error) or error.__class__.__name__
        sample_messages.append(f"#{index + 1}: {message}")
    if len(failures) > len(sample_messages):
        sample_messages.append(f"... and {len(failures) - len(sample_messages)} more")
    suffix = "; ".join(sample_messages) if sample_messages else "no image data"
    return f"{len(failures)} of {total} image generation requests failed: {suffix}"


async def call_batched_image_generation_api(
    *,
    job_id: str,
    api_url: str,
    api_key: str,
    api_path: str,
    api_preset_name: str,
    req: GenerateRequest,
    socks5_proxy: str,
) -> ImageJobOutcome:
    total = req.n
    completed = 0

    def publish_batch_progress():
        set_generate_job_progress(
            job_id,
            "waiting_for_api",
            f"Generating images ({completed}/{total} completed)",
            "generation",
        )

    publish_batch_progress()

    async def call_one(index: int) -> list[GalleryEntry]:
        nonlocal completed
        child_req = req.model_copy(update={"n": 1})
        try:
            async with get_upstream_request_semaphore():
                return await proxy.call_image_generation_api(
                    api_url,
                    api_key,
                    api_path,
                    child_req,
                    api_preset_name,
                    lambda _stage, _message: None,
                    socks5_proxy=socks5_proxy,
                )
        finally:
            completed += 1
            publish_batch_progress()

    results = await asyncio.gather(
        *(call_one(index) for index in range(total)),
        return_exceptions=True,
    )
    entries: list[GalleryEntry] = []
    failures: list[tuple[int, BaseException]] = []
    for index, result in enumerate(results):
        if isinstance(result, BaseException):
            if isinstance(result, asyncio.CancelledError):
                raise result
            failures.append((index, result))
        else:
            entries.extend(result)

    if not entries:
        if failures:
            summary = summarize_batch_generation_failures(failures, total)
            if all(isinstance(error, proxy.UpstreamApiError) for _index, error in failures):
                raise proxy.UpstreamApiError(summary)
            raise RuntimeError(summary)
        raise proxy.UpstreamApiError("No image data in upstream response")

    if not failures:
        return ImageJobOutcome(entries=entries)

    failure_summary = summarize_batch_generation_failures(failures, total)
    return ImageJobOutcome(
        entries=entries,
        success_message=(
            f"Generated {len(entries)} of {total} requested images; "
            f"{len(failures)} failed"
        ),
        error_message=failure_summary,
    )


async def _run_image_job(
    *,
    job_id: str,
    api_url: str,
    api_path: str,
    api_preset_name: str,
    req: GenerateRequest | EditRequest,
    operation: str,
    start_stage: str,
    start_message: str,
    success_message: str,
    failed_stage: str,
    cancel_message: str,
    log_action: str,
    call_upstream: Callable[[], Awaitable[list[GalleryEntry] | ImageJobOutcome]],
):
    started_at = time.monotonic()
    stage_timer = JobStageTimer()
    outcome = ImageJobOutcome(entries=[])

    try:
        async with get_generate_job_semaphore():
            if job_id not in app.state.generate_jobs:
                logger.info("Image %s skipped after cancellation: job_id=%s", log_action, job_id)
                return
            started_at = time.monotonic()
            store_generate_job(
                job_id,
                {
                    "status": "running",
                    "stage": start_stage,
                    "message": start_message,
                    "operation": operation,
                    "prompt": req.prompt,
                    "size": req.size,
                    "started_at": utc_now(),
                    "model": req.model,
                    "quality": req.quality,
                    "output_format": req.output_format,
                    "output_compression": req.output_compression,
                    "response_format": req.response_format,
                    "n": req.n,
                    "api_path": api_path,
                    "api_preset_name": api_preset_name,
                },
            )
            metrics.increment(f"image_jobs.{operation}.started")
            with use_job_stage_timer(stage_timer):
                outcome = normalize_image_job_outcome(await call_upstream())
                if not outcome.entries:
                    raise proxy.UpstreamApiError("No image data in upstream response")
            duration_seconds = time.monotonic() - started_at
            duration = f"{duration_seconds:.2f}s"
    except asyncio.CancelledError:
        existing_job = app.state.generate_jobs.get(job_id) or storage.get_generate_job(job_id) or {}
        existing_status = existing_job.get("status")
        status = (
            existing_status
            if existing_status in {"cancelled", "interrupted"}
            else "cancelled"
        )
        message = str(existing_job.get("message") or cancel_message)
        stage = str(existing_job.get("stage") or "cancelled")
        stage_timings = stage_timer.snapshot()
        duration_seconds = time.monotonic() - started_at
        metrics.increment(f"image_jobs.{operation}.cancelled")
        metrics.observe_ms("image_job.duration", duration_seconds * 1000)
        metrics.observe_job_stage_timings(stage_timings)
        store_generate_job(
            job_id,
            {
                "status": status,
                "stage": stage,
                "message": message,
                "operation": operation,
                "completed_at": utc_now(),
                "duration": f"{duration_seconds:.2f}s",
                "stage_timings": stage_timings,
                "error": message,
            },
        )
        trim_generate_jobs()
        logger.info("Image %s cancelled: job_id=%s", log_action, job_id)
        raise
    except Exception as e:
        if job_id not in app.state.generate_jobs:
            logger.info("Image %s stopped after cancellation: job_id=%s", log_action, job_id)
            return
        error_message = get_exception_message(e)
        status = "upstream_error" if isinstance(e, proxy.UpstreamApiError) else "error"
        stage_timings = stage_timer.snapshot()
        duration_seconds = time.monotonic() - started_at
        metrics.increment(f"image_jobs.{operation}.failed")
        metrics.observe_ms("image_job.duration", duration_seconds * 1000)
        metrics.observe_job_stage_timings(stage_timings)
        logger.exception(
            "Image %s failed: job_id=%s error_type=%s api_url=%s api_path=%s model=%s size=%s quality=%s output_format=%s response_format=%s n=%s",
            log_action,
            job_id,
            e.__class__.__name__,
            ssrf.redact_url(api_url),
            api_path,
            req.model,
            req.size,
            req.quality,
            req.output_format,
            req.response_format,
            req.n,
        )
        store_generate_job(
            job_id,
            {
                "status": status,
                "stage": failed_stage,
                "message": error_message,
                "operation": operation,
                "completed_at": utc_now(),
                "duration": f"{duration_seconds:.2f}s",
                "stage_timings": stage_timings,
                "error": error_message,
            },
        )
        trim_generate_jobs()
        return

    if job_id not in app.state.generate_jobs:
        logger.info("Image %s result discarded after cancellation: job_id=%s", log_action, job_id)
        return

    set_generate_job_progress(
        job_id,
        "finalizing_preview",
        "Finalizing preview image",
        operation,
    )
    completed_at = beijing_now()
    stage_timings = stage_timer.snapshot()
    metrics.increment(f"image_jobs.{operation}.succeeded")
    metrics.observe_ms("image_job.duration", duration_seconds * 1000)
    metrics.observe_job_stage_timings(stage_timings)
    updated_entries = [
        storage.update_gallery_entry(
            entry.id,
            {"duration": duration, "completed_at": completed_at, "n": req.n},
        )
        or entry
        for entry in outcome.entries
    ]
    first_entry = updated_entries[0]
    job_images = [gallery_entry_job_image(entry) for entry in updated_entries]
    job_update = {
        "status": "success",
        "stage": "completed",
        "message": outcome.success_message or success_message,
        "operation": operation,
        "image_id": first_entry.id,
        "image_url": f"/api/image/{first_entry.filename}",
        "images": job_images,
        "prompt": first_entry.prompt,
        "size": first_entry.size,
        "image_width": first_entry.image_width,
        "image_height": first_entry.image_height,
        "model": first_entry.model,
        "quality": first_entry.quality,
        "output_format": first_entry.output_format,
        "output_compression": first_entry.output_compression,
        "response_format": first_entry.response_format,
        "n": req.n,
        "api_path": first_entry.api_path,
        "api_preset_name": first_entry.api_preset_name,
        "duration": duration,
        "stage_timings": stage_timings,
        "completed_at": completed_at,
    }
    if outcome.error_message:
        job_update["error"] = outcome.error_message
    store_generate_job(job_id, job_update)
    trim_generate_jobs()


async def run_generate_job(
    job_id: str,
    api_url: str,
    api_key: str,
    api_path: str,
    api_preset_name: str,
    req: GenerateRequest,
    socks5_proxy: str = "",
):
    async def call_generation_upstream() -> list[GalleryEntry] | ImageJobOutcome:
        if req.n <= 1:
            async with get_upstream_request_semaphore():
                return await proxy.call_image_generation_api(
                    api_url,
                    api_key,
                    api_path,
                    req,
                    api_preset_name,
                    lambda stage, message: set_generate_job_progress(
                        job_id,
                        stage,
                        message,
                        "generation",
                    ),
                    socks5_proxy=socks5_proxy,
                )
        return await call_batched_image_generation_api(
            job_id=job_id,
            api_url=api_url,
            api_key=api_key,
            api_path=api_path,
            api_preset_name=api_preset_name,
            req=req,
            socks5_proxy=socks5_proxy,
        )

    await _run_image_job(
        job_id=job_id,
        api_url=api_url,
        api_path=api_path,
        api_preset_name=api_preset_name,
        req=req,
        operation="generation",
        start_stage="starting_generation",
        start_message="Starting image generation",
        success_message="Image generation completed",
        failed_stage="generation_failed",
        cancel_message="Generation job cancelled",
        log_action="generation",
        call_upstream=call_generation_upstream,
    )


async def run_edit_job(
    job_id: str,
    api_url: str,
    api_key: str,
    api_preset_name: str,
    req: EditRequest,
    image_sources: list[EditImageSource],
    image_source_bytes: int,
    socks5_proxy: str = "",
):
    async def call_edit_upstream() -> list[GalleryEntry]:
        async with get_upstream_request_semaphore():
            return await proxy.call_image_edit_api(
                api_url,
                api_key,
                req,
                image_sources,
                api_preset_name,
                lambda stage, message: set_generate_job_progress(
                    job_id,
                    stage,
                    message,
                    "edit",
                ),
                socks5_proxy=socks5_proxy,
            )

    try:
        await _run_image_job(
            job_id=job_id,
            api_url=api_url,
            api_path="/v1/images/edits",
            api_preset_name=api_preset_name,
            req=req,
            operation="edit",
            start_stage="starting_edit",
            start_message="Starting image edit",
            success_message="Image edit completed",
            failed_stage="edit_failed",
            cancel_message="Image edit job cancelled",
            log_action="edit",
            call_upstream=call_edit_upstream,
        )
    finally:
        for source in image_sources:
            source.temp_path.unlink(missing_ok=True)
        release_pending_edit_source_bytes(image_source_bytes)


def image_unit_lease_expires_at() -> str:
    return datetime_from_monotonic_delta(config.IMAGE_JOB_UNIT_LEASE_SECONDS)


def datetime_from_monotonic_delta(seconds: float) -> str:
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(seconds=max(1.0, seconds))).isoformat()


async def run_claimed_image_unit(unit: dict, worker_id: str):
    unit_id = str(unit["unit_id"])
    parent_job_id = str(unit["parent_job_id"])
    operation = str(unit.get("operation") or "generation")
    stage_timer = JobStageTimer()
    started_at = time.monotonic()
    parent = storage.get_generate_job(parent_job_id) or {}
    req = rebuild_request(operation, unit.get("request") or {})
    api_path = str(unit.get("api_path") or parent.get("api_path") or "/v1/images/generations")
    api_preset_name = str(unit.get("api_preset_name") or parent.get("api_preset_name") or "")
    preset = get_preset_for_unit(unit)
    if not preset:
        raise RuntimeError("API preset not found for image unit")
    api_url = ssrf.normalize_upstream_base_url(str(preset.get("api_url") or "").rstrip("/"))
    api_key = get_effective_preset_api_key(preset)
    socks5_proxy = get_upstream_socks5_proxy()

    def progress(stage: str, message: str):
        storage.update_image_job_unit_progress(
            unit_id,
            stage=stage,
            message=message,
            claim_expires_at=image_unit_lease_expires_at(),
        )
        aggregate_parent_image_job(parent_job_id)

    try:
        if (storage.get_generate_job(parent_job_id) or {}).get("status") == "cancelled":
            raise asyncio.CancelledError()
        start_stage = "starting_edit" if operation == "edit" else "starting_generation"
        start_message = "Starting image edit" if operation == "edit" else "Starting image generation"
        storage.update_image_job_unit_progress(
            unit_id,
            stage=start_stage,
            message=start_message,
            claim_expires_at=image_unit_lease_expires_at(),
        )
        store_generate_job(
            parent_job_id,
            {
                "status": "running",
                "stage": start_stage,
                "message": start_message,
                "operation": operation,
                "started_at": parent.get("started_at") or utc_now(),
            },
        )
        if int(parent.get("n") or 1) > 1:
            aggregate_parent_image_job(parent_job_id, force_publish=True)
        metrics.increment(f"image_jobs.{operation}.started")
        with use_job_stage_timer(stage_timer):
            if operation == "edit":
                image_sources = [
                    edit_source_from_payload(source)
                    for source in unit.get("edit_sources") or []
                ]
                if not image_sources:
                    raise proxy.UpstreamApiError("At least one edit source image is required")
                entries = await proxy.call_image_edit_api(
                    api_url,
                    api_key,
                    req,  # type: ignore[arg-type]
                    image_sources,
                    api_preset_name,
                    progress,
                    socks5_proxy=socks5_proxy,
                )
            else:
                entries = await proxy.call_image_generation_api(
                    api_url,
                    api_key,
                    api_path,
                    req,  # type: ignore[arg-type]
                    api_preset_name,
                    progress,
                    socks5_proxy=socks5_proxy,
                )
            if not entries:
                raise proxy.UpstreamApiError("No image data in upstream response")
        duration_seconds = time.monotonic() - started_at
        duration = f"{duration_seconds:.2f}s"
        completed_at = beijing_now()
        updated_entries = [
            storage.update_gallery_entry(
                entry.id,
                {"duration": duration, "completed_at": completed_at, "n": parent.get("n") or req.n},
            )
            or entry
            for entry in entries
        ]
        result_images = [gallery_entry_job_result(entry) for entry in updated_entries]
        stage_timings = stage_timer.snapshot()
        metrics.increment(f"image_jobs.{operation}.succeeded")
        metrics.observe_ms("image_job.duration", duration_seconds * 1000)
        metrics.observe_job_stage_timings(stage_timings)
        if (storage.get_generate_job(parent_job_id) or {}).get("status") == "cancelled":
            storage.fail_image_job_unit(
                unit_id,
                status="cancelled",
                stage="cancelled",
                message="Generation job cancelled",
                error="Generation job cancelled",
                stage_timings=stage_timings,
                duration=duration,
                completed_at=utc_now(),
            )
            return
        storage.complete_image_job_unit(
            unit_id,
            result={"images": result_images},
            stage_timings=stage_timings,
            duration=duration,
            completed_at=completed_at,
        )
    except asyncio.CancelledError:
        duration_seconds = time.monotonic() - started_at
        stage_timings = stage_timer.snapshot()
        metrics.increment(f"image_jobs.{operation}.cancelled")
        storage.fail_image_job_unit(
            unit_id,
            status="cancelled",
            stage="cancelled",
            message="Generation job cancelled",
            error="Generation job cancelled",
            stage_timings=stage_timings,
            duration=f"{duration_seconds:.2f}s",
            completed_at=utc_now(),
        )
    except Exception as e:
        error_message = get_exception_message(e)
        status = "upstream_error" if isinstance(e, proxy.UpstreamApiError) else "error"
        duration_seconds = time.monotonic() - started_at
        stage_timings = stage_timer.snapshot()
        if (storage.get_generate_job(parent_job_id) or {}).get("status") == "cancelled":
            storage.fail_image_job_unit(
                unit_id,
                status="cancelled",
                stage="cancelled",
                message="Generation job cancelled",
                error="Generation job cancelled",
                stage_timings=stage_timings,
                duration=f"{duration_seconds:.2f}s",
                completed_at=utc_now(),
            )
            return
        metrics.increment(f"image_jobs.{operation}.failed")
        metrics.observe_ms("image_job.duration", duration_seconds * 1000)
        metrics.observe_job_stage_timings(stage_timings)
        logger.exception(
            "Image unit failed: unit_id=%s parent_job_id=%s worker_id=%s error_type=%s",
            unit_id,
            parent_job_id,
            worker_id,
            e.__class__.__name__,
        )
        storage.fail_image_job_unit(
            unit_id,
            status=status,
            stage="generation_failed" if operation == "generation" else "edit_failed",
            message=error_message,
            error=error_message,
            stage_timings=stage_timings,
            duration=f"{duration_seconds:.2f}s",
            completed_at=utc_now(),
        )
    finally:
        aggregate_parent_image_job(parent_job_id, force_publish=True)


async def run_image_unit_dispatcher(worker_id: str):
    logger.info("Image unit dispatcher started: worker_id=%s", worker_id)
    active_tasks: set[asyncio.Task] = set()
    try:
        while True:
            active_tasks = {task for task in active_tasks if not task.done()}
            storage.mark_worker_heartbeat(worker_id, len(active_tasks))
            while len(active_tasks) < config.MAX_ACTIVE_GENERATE_JOBS:
                unit = storage.claim_next_image_job_unit(
                    worker_id=worker_id,
                    lease_expires_at=image_unit_lease_expires_at(),
                    now=utc_now(),
                    running_limit=config.MAX_ACTIVE_GENERATE_JOBS,
                )
                if not unit:
                    break
                task = asyncio.create_task(run_claimed_image_unit(unit, worker_id))
                active_tasks.add(task)
            await asyncio.sleep(config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        raise
