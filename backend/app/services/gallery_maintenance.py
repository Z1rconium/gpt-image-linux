import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..api.app_state import app
from ..core import settings as config
from ..core.observability import metrics
from ..core.utils import utc_now
from ..integrations.r2 import config as r2_config
from ..repositories.coordination import (
    acquire_background_lease,
    cleanup_expired_gallery_jobs,
    cleanup_stale_gallery_jobs,
    count_active_gallery_jobs,
    list_gallery_job_ids_with_files,
    release_background_lease,
)
from ..repositories.gallery.mutations import cleanup_orphan_gallery_files
from ..repositories.gallery.queries import get_gallery_count
from ..repositories.gallery.sync_state import (
    count_gallery_r2_sync_rows,
)
from ..repositories.settings import load_r2_backup_settings
from ..repositories.thumbnail_jobs import (
    THUMBNAIL_JOB_LEASE_SECONDS,
    claim_next_thumbnail_job,
    complete_thumbnail_job,
    fail_thumbnail_job,
    generate_thumbnail_for_image,
)
from .claim_loop import run_claim_loop
from .gallery_common import (
    AI_ANALYZE_JOB_KIND,
    AI_ANALYZE_JOB_TTL_SECONDS,
    EXPORT_JOB_GC_INTERVAL_SECONDS,
    EXPORT_JOB_TTL_SECONDS,
    GALLERY_FILE_GC_INTERVAL_SECONDS,
    IMPORT_JOB_TTL_SECONDS,
    MAX_ACTIVE_SYNC_JOBS,
    SCHEDULED_R2_SYNC_DISABLED_POLL_SECONDS,
    SYNC_JOB_TTL_SECONDS,
    THUMBNAIL_DISPATCH_INTERVAL_SECONDS,
    THUMBNAIL_DISPATCH_MAX_IDLE_BACKOFF_SECONDS,
    _unlink_trusted_gallery_job_path,
)
from .gallery_jobs import (
    _background_task_lease_expires_at,
    _create_gallery_sync_job,
    _next_background_task_error_backoff,
    _sleep_while_renewing_background_lease,
    run_gallery_export_dispatcher,
    run_gallery_import_dispatcher,
    run_gallery_sync_dispatcher,
)

logger = logging.getLogger(__name__)

def get_thumbnail_dispatcher_kick_event() -> asyncio.Event:
    event = getattr(app.state, "thumbnail_dispatcher_kick", None)
    if event is None:
        event = asyncio.Event()
        app.state.thumbnail_dispatcher_kick = event
    return event


def kick_thumbnail_dispatcher() -> None:
    task = getattr(app.state, "thumbnail_dispatcher_task", None)
    if task and not task.done():
        get_thumbnail_dispatcher_kick_event().set()
        return
    worker_id = getattr(app.state, "worker_id", f"{os.getpid()}-{id(app)}")
    app.state.thumbnail_dispatcher_task = asyncio.create_task(
        run_thumbnail_dispatcher(worker_id)
    )


def _thumbnail_job_lease_expires_at() -> str:
    return (
        datetime.now(timezone.utc)
        + timedelta(seconds=THUMBNAIL_JOB_LEASE_SECONDS)
    ).isoformat()


async def run_thumbnail_dispatcher(worker_id: str) -> None:
    logger.info("Thumbnail dispatcher started: worker_id=%s", worker_id)
    async def claim_thumbnail_job():
        owner = f"thumbnail:{worker_id}:{uuid.uuid4()}"
        job = await asyncio.to_thread(
            claim_next_thumbnail_job,
            owner=owner,
            lease_expires_at=_thumbnail_job_lease_expires_at(),
            now=utc_now(),
        )
        if job:
            job["lease_owner"] = owner
        return job

    async def run_thumbnail_job(job: dict):
        owner = str(job.get("lease_owner") or "")
        filename = str(job.get("filename") or "")
        thumbnail_filename = await asyncio.to_thread(
            generate_thumbnail_for_image,
            filename,
        )
        if thumbnail_filename:
            await asyncio.to_thread(
                complete_thumbnail_job,
                filename,
                owner=owner,
            )
        else:
            await asyncio.to_thread(
                fail_thumbnail_job,
                filename,
                owner=owner,
                error="thumbnail generation returned no file",
            )

    await run_claim_loop(
        claim_fn=claim_thumbnail_job,
        run_fn=run_thumbnail_job,
        running_limit=1,
        idle_interval=THUMBNAIL_DISPATCH_INTERVAL_SECONDS,
        max_backoff=THUMBNAIL_DISPATCH_MAX_IDLE_BACKOFF_SECONDS,
        kick_event=get_thumbnail_dispatcher_kick_event(),
        logger=logger,
        error_message="Thumbnail dispatcher error",
        task_name="thumbnail job",
    )


def kick_gallery_file_gc() -> None:
    task = getattr(app.state, "gallery_file_gc_task", None)
    if task and not task.done():
        get_gallery_file_gc_kick_event().set()
        return
    worker_id = getattr(app.state, "worker_id", f"{os.getpid()}-{id(app)}")
    app.state.gallery_file_gc_task = asyncio.create_task(
        run_gallery_file_gc(worker_id, initial_delay_seconds=0.0)
    )


def get_gallery_file_gc_kick_event() -> asyncio.Event:
    event = getattr(app.state, "gallery_file_gc_kick", None)
    if event is None:
        event = asyncio.Event()
        app.state.gallery_file_gc_kick = event
    return event


async def _wait_for_gallery_file_gc_wakeup(delay: float) -> None:
    if delay <= 0:
        return
    event = get_gallery_file_gc_kick_event()
    try:
        await asyncio.wait_for(event.wait(), timeout=delay)
    except asyncio.TimeoutError:
        return
    finally:
        event.clear()


def kick_gallery_job_dispatchers() -> None:
    for name, starter in (
        ("gallery_export_dispatcher_task", run_gallery_export_dispatcher),
        ("gallery_sync_dispatcher_task", run_gallery_sync_dispatcher),
        ("gallery_import_dispatcher_task", run_gallery_import_dispatcher),
    ):
        task = getattr(app.state, name, None)
        if task and not task.done():
            continue
        worker_id = getattr(app.state, "worker_id", f"{os.getpid()}-{id(app)}")
        setattr(app.state, name, asyncio.create_task(starter(worker_id)))


def _r2_sync_interval_hours(r2_settings: dict | None) -> int:
    if not isinstance(r2_settings, dict):
        return 0
    try:
        interval_hours = int(r2_settings.get("sync_interval_hours") or 0)
    except (TypeError, ValueError):
        return 0
    return interval_hours if interval_hours > 0 else 0


async def _scheduled_gallery_r2_sync_delay_seconds() -> int:
    try:
        r2_settings = await asyncio.to_thread(load_r2_backup_settings)
    except Exception:
        logger.warning("Failed to load R2 settings for scheduled sync", exc_info=True)
        return SCHEDULED_R2_SYNC_DISABLED_POLL_SECONDS
    if not r2_settings.get("enabled"):
        return SCHEDULED_R2_SYNC_DISABLED_POLL_SECONDS
    interval_hours = _r2_sync_interval_hours(r2_settings)
    if interval_hours <= 0:
        return SCHEDULED_R2_SYNC_DISABLED_POLL_SECONDS
    return interval_hours * 3600


async def _run_scheduled_gallery_r2_sync_once() -> dict[str, object]:
    r2_settings = await asyncio.to_thread(load_r2_backup_settings)
    interval_hours = _r2_sync_interval_hours(r2_settings)
    if not r2_settings.get("enabled") or interval_hours <= 0:
        return {"started": False, "reason": "disabled"}

    try:
        effective = await asyncio.to_thread(
            r2_config.resolve_r2_backup_settings,
            r2_settings,
            require_enabled=True,
        )
    except r2_config.R2ConfigurationError as e:
        logger.info("Skipping scheduled R2 gallery sync: %s", e)
        return {"started": False, "reason": "invalid_config"}

    gallery_count = await asyncio.to_thread(get_gallery_count)
    if gallery_count <= 0:
        return {"started": False, "reason": "empty_gallery"}
    total_count = await asyncio.to_thread(
        count_gallery_r2_sync_rows,
        key_prefix=effective.key_prefix,
        full_reconcile=False,
    )
    if total_count <= 0:
        return {"started": False, "reason": "no_changes"}

    active_count = await asyncio.to_thread(count_active_gallery_jobs, "sync")
    if active_count >= MAX_ACTIVE_SYNC_JOBS:
        return {"started": False, "reason": "active_sync"}
    job = await asyncio.to_thread(
        _create_gallery_sync_job,
        total_count,
        {"full_reconcile": False, "dry_run": False},
    )
    kick_gallery_job_dispatchers()
    logger.info("Queued scheduled R2 gallery sync job %s", job["job_id"])
    return {"started": True, "job_id": job["job_id"]}


async def run_gallery_r2_scheduled_sync(worker_id: str) -> None:
    lease_name = "gallery_r2_scheduled_sync"
    error_backoff_seconds = 0.0
    while True:
        try:
            acquired = await asyncio.to_thread(
                acquire_background_lease,
                name=lease_name,
                owner=worker_id,
                lease_expires_at=_background_task_lease_expires_at(),
            )
            if not acquired:
                error_backoff_seconds = 0.0
                await asyncio.sleep(SCHEDULED_R2_SYNC_DISABLED_POLL_SECONDS)
                continue
            delay_seconds = await _scheduled_gallery_r2_sync_delay_seconds()
            try:
                still_leader = await _sleep_while_renewing_background_lease(
                    name=lease_name,
                    owner=worker_id,
                    delay_seconds=delay_seconds,
                )
                if not still_leader:
                    error_backoff_seconds = 0.0
                    continue
                await _run_scheduled_gallery_r2_sync_once()
            finally:
                await asyncio.to_thread(
                    release_background_lease,
                    name=lease_name,
                    owner=worker_id,
                )
            error_backoff_seconds = 0.0
        except asyncio.CancelledError:
            raise
        except Exception:
            error_backoff_seconds = _next_background_task_error_backoff(error_backoff_seconds)
            logger.warning(
                "Scheduled R2 gallery sync failed before job creation; retrying in %.1f seconds",
                error_backoff_seconds,
                exc_info=True,
            )
            await asyncio.sleep(error_backoff_seconds)


async def run_gallery_file_gc(
    worker_id: str,
    *,
    initial_delay_seconds: float = GALLERY_FILE_GC_INTERVAL_SECONDS,
) -> None:
    lease_name = "gallery_file_gc"
    delay = max(0.0, initial_delay_seconds)
    while True:
        try:
            await _wait_for_gallery_file_gc_wakeup(delay)
            acquired = await asyncio.to_thread(
                acquire_background_lease,
                name=lease_name,
                owner=worker_id,
                lease_expires_at=_background_task_lease_expires_at(),
            )
            if not acquired:
                delay = GALLERY_FILE_GC_INTERVAL_SECONDS
                continue
            try:
                result = await asyncio.to_thread(cleanup_orphan_gallery_files)
                if result.get("removed_images") or result.get("removed_thumbnails") or result.get("failed"):
                    metrics.increment("gallery.file_gc_runs")
            finally:
                await asyncio.to_thread(
                    release_background_lease,
                    name=lease_name,
                    owner=worker_id,
                )
            delay = GALLERY_FILE_GC_INTERVAL_SECONDS
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Gallery file GC error", exc_info=True)
            delay = GALLERY_FILE_GC_INTERVAL_SECONDS


async def gc_gallery_export_jobs(worker_id: str) -> None:
    """Periodically clean up completed export/sync jobs and orphan ZIP files."""
    lease_name = "gallery_export_gc"
    error_backoff_seconds = 0.0
    while True:
        try:
            acquired = await asyncio.to_thread(
                acquire_background_lease,
                name=lease_name,
                owner=worker_id,
                lease_expires_at=_background_task_lease_expires_at(),
            )
            if not acquired:
                error_backoff_seconds = 0.0
                await asyncio.sleep(EXPORT_JOB_GC_INTERVAL_SECONDS)
                continue
            try:
                still_leader = await _sleep_while_renewing_background_lease(
                    name=lease_name,
                    owner=worker_id,
                    delay_seconds=EXPORT_JOB_GC_INTERVAL_SECONDS,
                )
                if not still_leader:
                    error_backoff_seconds = 0.0
                    continue
                stale_exports = await asyncio.to_thread(
                    cleanup_stale_gallery_jobs,
                    "export",
                    EXPORT_JOB_TTL_SECONDS,
                )
                stale_syncs = await asyncio.to_thread(
                    cleanup_stale_gallery_jobs,
                    "sync",
                    SYNC_JOB_TTL_SECONDS,
                )
                stale_imports = await asyncio.to_thread(
                    cleanup_stale_gallery_jobs,
                    "import",
                    IMPORT_JOB_TTL_SECONDS,
                )
                stale_direct_exports = await asyncio.to_thread(
                    cleanup_stale_gallery_jobs,
                    "export_direct",
                    EXPORT_JOB_TTL_SECONDS,
                )
                stale_ai_analyze_jobs = await asyncio.to_thread(
                    cleanup_stale_gallery_jobs,
                    AI_ANALYZE_JOB_KIND,
                    AI_ANALYZE_JOB_TTL_SECONDS,
                )
                expired_direct_slots = await asyncio.to_thread(
                    cleanup_expired_gallery_jobs,
                    "export_direct",
                )
                for job in stale_exports:
                    path = job.get("path")
                    if path:
                        _unlink_trusted_gallery_job_path(
                            path,
                            kind="export",
                            job_id=str(job.get("job_id") or ""),
                        )
                for job in stale_imports:
                    path = job.get("path")
                    if path:
                        _unlink_trusted_gallery_job_path(
                            path,
                            kind="import",
                            job_id=str(job.get("job_id") or ""),
                        )
                if (
                    stale_exports
                    or stale_syncs
                    or stale_imports
                    or stale_direct_exports
                    or stale_ai_analyze_jobs
                    or expired_direct_slots
                ):
                    logger.info(
                        "GC cleaned up %d gallery export job(s), %d sync job(s), %d import job(s), %d completed direct export job(s), %d AI analyze job(s), and %d direct export slot(s)",
                        len(stale_exports),
                        len(stale_syncs),
                        len(stale_imports),
                        len(stale_direct_exports),
                        len(stale_ai_analyze_jobs),
                        len(expired_direct_slots),
                    )
                exports_dir = Path(config.DATA_DIR) / "exports"
                if exports_dir.exists():
                    known_ids = await asyncio.to_thread(list_gallery_job_ids_with_files, "export")
                    for zip_path in exports_dir.glob("*.zip"):
                        if zip_path.stem not in known_ids:
                            zip_path.unlink(missing_ok=True)
                            logger.info("GC removed orphan export file: %s", zip_path.name)
            finally:
                await asyncio.to_thread(
                    release_background_lease,
                    name=lease_name,
                    owner=worker_id,
                )
            error_backoff_seconds = 0.0
        except asyncio.CancelledError:
            raise
        except Exception:
            error_backoff_seconds = _next_background_task_error_backoff(error_backoff_seconds)
            logger.warning(
                "Gallery export GC error; retrying in %.1f seconds",
                error_backoff_seconds,
                exc_info=True,
            )
            await asyncio.sleep(error_backoff_seconds)



__all__ = [name for name in globals() if not name.startswith("__")]
