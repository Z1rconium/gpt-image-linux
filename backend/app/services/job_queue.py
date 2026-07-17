"""Image job request construction and durable queue admission."""

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from fastapi import HTTPException

from ..api.app_state import MAX_GENERATE_JOBS, app
from ..api.presets import (
    get_active_preset,
    get_api_presets,
    get_effective_preset_api_key,
    get_upstream_socks5_proxy,
    get_webhook_url,
    load_api_settings,
)
from ..core import settings as config
from ..core import validators as ssrf
from ..core.api_paths import normalize_default_model
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.observability import metrics
from ..core.utils import utc_now
from ..repositories.image_jobs import (
    EditSourceQueueFullError,
    ImageJobQueueFullError,
    aggregate_image_job_units,
    count_pending_image_job_units,
    enqueue_image_job,
    get_pending_edit_source_bytes as get_persisted_pending_edit_source_bytes,
    release_edit_source_reservation,
    trim_generate_jobs as trim_persisted_generate_jobs,
)
from ..repositories.gallery.queries import image_url_for_filename
from ..schemas.gallery import GalleryEntry
from ..schemas.generation import EditRequest, GenerateJobResponse, GenerateRequest
from .job_events import (
    get_job_subscribers,
    get_jobs_subscribers,
    publish_generate_job,
    store_generate_job,
    validate_job_webhook_url,
)

def kick_thumbnail_dispatcher() -> None:
    task = getattr(app.state, "thumbnail_dispatcher_task", None)
    event = getattr(app.state, "thumbnail_dispatcher_kick", None)
    if task and not task.done() and event is not None:
        event.set()


@dataclass(frozen=True)
class EditImageSource:
    temp_path: Path
    byte_size: int
    filename: str
    content_type: str


def trim_generate_jobs():
    trim_persisted_generate_jobs(MAX_GENERATE_JOBS)


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
    return get_persisted_pending_edit_source_bytes()


def get_max_pending_edit_source_bytes() -> int:
    return max(0, config.MAX_PENDING_EDIT_SOURCE_MB) * 1024 * 1024


def release_pending_edit_source_bytes(job_id: str):
    if not job_id:
        return
    release_edit_source_reservation(job_id)


def get_image_unit_dispatcher_kick_event() -> asyncio.Event:
    event = getattr(app.state, "image_unit_dispatcher_kick", None)
    if event is None:
        event = asyncio.Event()
        app.state.image_unit_dispatcher_kick = event
    return event


def kick_image_unit_dispatcher():
    event = getattr(app.state, "image_unit_dispatcher_kick", None)
    if event is not None:
        event.set()


def request_image_units(req: GenerateRequest | EditRequest) -> int:
    try:
        return max(1, int(req.n or 1))
    except (TypeError, ValueError):
        return 1


def snapshot_queue_metrics() -> dict[str, int]:
    jobs = app.state.generate_jobs or {}
    running_units, queued_units = count_pending_image_job_units()
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
        "image_url": (
            image_url_for_filename(entry.filename)
            or f"/api/image/{entry.filename}"
        ),
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
    load_api_settings()
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
    aggregate = aggregate_image_job_units(parent_job_id)
    paths: set[str] = set()
    for unit in aggregate.get("units", []):
        for source in unit.get("edit_sources") or []:
            path = str(source.get("temp_path") or "")
            if path:
                paths.add(path)
    for path in paths:
        Path(path).unlink(missing_ok=True)
    release_pending_edit_source_bytes(parent_job_id)


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
    pending_edit_source_bytes: int = 0,
    edit_sources_payload: list[dict] | None = None,
) -> GenerateJobResponse:
    load_api_settings()
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
        ssrf.normalize_upstream_base_url(api_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    get_effective_preset_api_key(active_preset)
    get_upstream_socks5_proxy()

    webhook_url = validate_job_webhook_url(req.webhook_url or get_webhook_url())
    image_units = request_image_units(req)
    job_id = str(uuid.uuid4())
    pending_job = build_pending_job(
        job_id=job_id,
        req=req,
        operation=operation,
        message=queued_message,
        api_path=resolved_api_path,
        api_preset_name=api_preset_name,
        image_units=image_units,
    )
    pending_job["webhook_url"] = webhook_url
    try:
        stored_job, _units = enqueue_image_job(
            parent_job=pending_job,
            operation=operation,
            request=build_request_payload(req),
            image_units=image_units,
            api_preset_id=active_preset_id,
            api_preset_name=api_preset_name,
            api_path=resolved_api_path,
            edit_sources=edit_sources_payload,
            pending_edit_source_bytes=pending_edit_source_bytes,
            max_active_generate_jobs=config.MAX_ACTIVE_GENERATE_JOBS,
            max_queued_generate_jobs=config.MAX_QUEUED_GENERATE_JOBS,
            max_pending_edit_source_bytes=get_max_pending_edit_source_bytes(),
        )
    except ImageJobQueueFullError as e:
        metrics.increment("image_jobs.rejected.queue_full")
        raise HTTPException(status_code=429, detail="Generation job queue is full") from e
    except EditSourceQueueFullError as e:
        metrics.increment("image_jobs.rejected.edit_source_full")
        raise HTTPException(status_code=429, detail="Edit source queue is full") from e
    except Exception:
        raise

    app.state.generate_jobs[job_id] = stored_job
    app.state.generate_job_last_persist_at.pop(job_id, None)
    metrics.increment(f"image_jobs.{operation}.queued")
    publish_generate_job(stored_job, list_debounce=False, list_reconcile=True)
    kick_image_unit_dispatcher()

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

    return queue_image_job(
        req=req,
        operation="edit",
        api_path="/v1/images/edits",
        queued_message="Queued image edit",
        pending_edit_source_bytes=image_source_bytes,
        edit_sources_payload=[edit_source_to_payload(source) for source in image_sources],
    )

