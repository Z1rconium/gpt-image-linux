"""Claimed image unit execution and parent-job aggregation."""

import asyncio
import logging
import time

from ..api.app_state import app
from ..api.presets import (
    get_effective_preset_api_key,
    get_exception_message,
    get_upstream_socks5_proxy,
)
from ..core import settings as config
from ..core import validators as ssrf
from ..core.observability import JobStageTimer, metrics, use_job_stage_timer
from ..core.utils import beijing_now, utc_now
from ..integrations.upstream import generation as proxy
from ..repositories.gallery.mutations import update_gallery_entry
from ..repositories.image_jobs import (
    complete_image_job_unit,
    fail_image_job_unit,
    get_generate_job,
    get_generate_job_with_unit_aggregate,
    update_image_job_unit_progress,
)
from .job_events import publish_generate_job, store_generate_job_async
from .blocking import run_db_operation
from .job_queue import (
    cleanup_parent_edit_sources,
    edit_source_from_payload,
    gallery_entry_job_result,
    get_preset_for_unit,
    kick_thumbnail_dispatcher,
    rebuild_request,
    summarize_unit_failures,
    trim_generate_jobs,
)

logger = logging.getLogger(__name__)

async def aggregate_parent_image_job(
    parent_job_id: str,
    *,
    force_publish: bool = False,
) -> dict | None:
    parent, aggregate = await run_db_operation(
        get_generate_job_with_unit_aggregate,
        parent_job_id,
        metric_name="aggregate_parent_image_job",
    )
    if not parent:
        return None
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
        job = await store_generate_job_async(parent_job_id, update)
        await run_db_operation(
            trim_generate_jobs,
            metric_name="trim_generate_jobs",
        )
        if operation == "edit":
            await run_db_operation(
                cleanup_parent_edit_sources,
                parent_job_id,
                metric_name="cleanup_parent_edit_sources",
            )
        return job

    if running_count > 0 or success_count > 0:
        stage = "waiting_for_api"
        message = (
            f"Editing images ({success_count}/{total} completed)"
            if operation == "edit"
            else f"Generating images ({success_count}/{total} completed)"
        )
        return await store_generate_job_async(
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
        return await store_generate_job_async(
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

    updated = {
        **job,
        "status": "running",
        "stage": stage,
        "message": message,
        "operation": operation,
        "updated_at": utc_now(),
    }
    app.state.generate_jobs[job_id] = updated
    publish_generate_job(updated, list_debounce=True, list_reconcile=False)


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
    parent = await run_db_operation(
        get_generate_job,
        parent_job_id,
        metric_name="get_generate_job_for_unit",
    ) or {}
    req = rebuild_request(operation, unit.get("request") or {})
    api_path = str(
        unit.get("api_path")
        or parent.get("api_path")
        or "/v1/images/generations"
    )
    api_preset_name = str(
        unit.get("api_preset_name") or parent.get("api_preset_name") or ""
    )
    preset = await run_db_operation(
        get_preset_for_unit,
        unit,
        metric_name="get_preset_for_image_unit",
    )
    if not preset:
        raise RuntimeError("API preset not found for image unit")
    api_url = ssrf.normalize_upstream_base_url(
        str(preset.get("api_url") or "").rstrip("/")
    )
    api_key = get_effective_preset_api_key(preset)
    socks5_proxy = get_upstream_socks5_proxy()

    progress_pending: tuple[str, str] | None = None
    progress_task: asyncio.Task | None = None
    last_progress_persist_at = 0.0

    async def persist_progress_updates():
        nonlocal progress_pending, progress_task, last_progress_persist_at
        try:
            while progress_pending is not None:
                delay = config.IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS - (
                    time.monotonic() - last_progress_persist_at
                )
                if delay > 0:
                    await asyncio.sleep(delay)
                stage, message = progress_pending
                progress_pending = None
                await run_db_operation(
                    update_image_job_unit_progress,
                    unit_id,
                    stage=stage,
                    message=message,
                    claim_expires_at=image_unit_lease_expires_at(),
                    metric_name="persist_image_job_progress",
                )
                last_progress_persist_at = time.monotonic()
                await aggregate_parent_image_job(parent_job_id)
        finally:
            progress_task = None
            if progress_pending is not None:
                progress_task = asyncio.create_task(persist_progress_updates())

    def progress(stage: str, message: str):
        nonlocal progress_pending, progress_task
        set_generate_job_progress(parent_job_id, stage, message, operation)
        progress_pending = (stage, message)
        if progress_task is None or progress_task.done():
            progress_task = asyncio.create_task(persist_progress_updates())

    async def flush_progress_updates():
        nonlocal progress_task
        task = progress_task
        if task is not None:
            await asyncio.gather(task, return_exceptions=False)
        while progress_task is not None:
            task = progress_task
            await asyncio.gather(task, return_exceptions=False)

    async def parent_was_cancelled() -> bool:
        current = await run_db_operation(
            get_generate_job,
            parent_job_id,
            metric_name="check_generate_job_cancelled",
        )
        return bool(current and current.get("status") == "cancelled")

    try:
        if await parent_was_cancelled():
            raise asyncio.CancelledError()
        start_stage = "starting_edit" if operation == "edit" else "starting_generation"
        start_message = (
            "Starting image edit" if operation == "edit" else "Starting image generation"
        )
        await run_db_operation(
            update_image_job_unit_progress,
            unit_id,
            stage=start_stage,
            message=start_message,
            claim_expires_at=image_unit_lease_expires_at(),
            metric_name="start_image_job_unit",
        )
        await store_generate_job_async(
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
            await aggregate_parent_image_job(parent_job_id, force_publish=True)
        metrics.increment(f"image_jobs.{operation}.started")
        with use_job_stage_timer(stage_timer):
            if operation == "edit":
                image_sources = [
                    edit_source_from_payload(source)
                    for source in unit.get("edit_sources") or []
                ]
                if not image_sources:
                    raise proxy.UpstreamApiError(
                        "At least one edit source image is required"
                    )
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

        await flush_progress_updates()
        duration_seconds = time.monotonic() - started_at
        duration = f"{duration_seconds:.2f}s"
        completed_at = beijing_now()

        def update_entries():
            return [
                update_gallery_entry(
                    entry.id,
                    {
                        "duration": duration,
                        "completed_at": completed_at,
                        "n": parent.get("n") or req.n,
                    },
                )
                or entry
                for entry in entries
            ]

        updated_entries = await run_db_operation(
            update_entries,
            metric_name="finalize_gallery_entries",
        )
        kick_thumbnail_dispatcher()
        result_images = [gallery_entry_job_result(entry) for entry in updated_entries]
        stage_timings = stage_timer.snapshot()
        metrics.increment(f"image_jobs.{operation}.succeeded")
        metrics.observe_ms("image_job.duration", duration_seconds * 1000)
        metrics.observe_job_stage_timings(stage_timings)
        if await parent_was_cancelled():
            await run_db_operation(
                fail_image_job_unit,
                unit_id,
                status="cancelled",
                stage="cancelled",
                message="Generation job cancelled",
                error="Generation job cancelled",
                stage_timings=stage_timings,
                duration=duration,
                completed_at=utc_now(),
                metric_name="cancel_completed_image_job_unit",
            )
            return
        await run_db_operation(
            complete_image_job_unit,
            unit_id,
            result={"images": result_images},
            stage_timings=stage_timings,
            duration=duration,
            completed_at=completed_at,
            metric_name="complete_image_job_unit",
        )
    except asyncio.CancelledError:
        duration_seconds = time.monotonic() - started_at
        stage_timings = stage_timer.snapshot()
        metrics.increment(f"image_jobs.{operation}.cancelled")
        await run_db_operation(
            fail_image_job_unit,
            unit_id,
            status="cancelled",
            stage="cancelled",
            message="Generation job cancelled",
            error="Generation job cancelled",
            stage_timings=stage_timings,
            duration=f"{duration_seconds:.2f}s",
            completed_at=utc_now(),
            metric_name="cancel_image_job_unit",
        )
    except Exception as error:
        error_message = get_exception_message(error)
        status = (
            "upstream_error" if isinstance(error, proxy.UpstreamApiError) else "error"
        )
        duration_seconds = time.monotonic() - started_at
        stage_timings = stage_timer.snapshot()
        cancelled = await parent_was_cancelled()
        if not cancelled:
            metrics.increment(f"image_jobs.{operation}.failed")
            metrics.observe_ms("image_job.duration", duration_seconds * 1000)
            metrics.observe_job_stage_timings(stage_timings)
            logger.exception(
                "Image unit failed: unit_id=%s parent_job_id=%s worker_id=%s error_type=%s",
                unit_id,
                parent_job_id,
                worker_id,
                error.__class__.__name__,
            )
        await run_db_operation(
            fail_image_job_unit,
            unit_id,
            status="cancelled" if cancelled else status,
            stage=(
                "cancelled"
                if cancelled
                else "generation_failed" if operation == "generation" else "edit_failed"
            ),
            message="Generation job cancelled" if cancelled else error_message,
            error="Generation job cancelled" if cancelled else error_message,
            stage_timings=stage_timings,
            duration=f"{duration_seconds:.2f}s",
            completed_at=utc_now(),
            metric_name="fail_image_job_unit",
        )
    finally:
        await flush_progress_updates()
        await aggregate_parent_image_job(parent_job_id, force_publish=True)
