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
    aggregate_image_job_units,
    complete_image_job_unit,
    fail_image_job_unit,
    get_generate_job,
    update_image_job_unit_progress,
)
from .job_events import store_generate_job
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

def aggregate_parent_image_job(parent_job_id: str, *, force_publish: bool = False) -> dict | None:
    parent = get_generate_job(parent_job_id)
    if not parent:
        return None
    aggregate = aggregate_image_job_units(parent_job_id)
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
    parent = get_generate_job(parent_job_id) or {}
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
        update_image_job_unit_progress(
            unit_id,
            stage=stage,
            message=message,
            claim_expires_at=image_unit_lease_expires_at(),
        )
        aggregate_parent_image_job(parent_job_id)

    try:
        if (get_generate_job(parent_job_id) or {}).get("status") == "cancelled":
            raise asyncio.CancelledError()
        start_stage = "starting_edit" if operation == "edit" else "starting_generation"
        start_message = "Starting image edit" if operation == "edit" else "Starting image generation"
        update_image_job_unit_progress(
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
            update_gallery_entry(
                entry.id,
                {"duration": duration, "completed_at": completed_at, "n": parent.get("n") or req.n},
            )
            or entry
            for entry in entries
        ]
        kick_thumbnail_dispatcher()
        result_images = [gallery_entry_job_result(entry) for entry in updated_entries]
        stage_timings = stage_timer.snapshot()
        metrics.increment(f"image_jobs.{operation}.succeeded")
        metrics.observe_ms("image_job.duration", duration_seconds * 1000)
        metrics.observe_job_stage_timings(stage_timings)
        if (get_generate_job(parent_job_id) or {}).get("status") == "cancelled":
            fail_image_job_unit(
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
        complete_image_job_unit(
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
        fail_image_job_unit(
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
        if (get_generate_job(parent_job_id) or {}).get("status") == "cancelled":
            fail_image_job_unit(
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
        fail_image_job_unit(
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

