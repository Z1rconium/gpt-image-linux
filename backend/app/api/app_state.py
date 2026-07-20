import asyncio
import logging
import os
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI

from ..core import secrets
from ..core import security as auth
from ..core import settings as config
from ..core import overall_config
from ..repositories.coordination import (
    acquire_background_lease,
    complete_background_lease,
    list_gallery_job_ids_with_files,
    release_background_lease,
)
from ..repositories.db import close_database_connections, verify_storage_writable
from ..repositories.gallery.mutations import (
    backfill_missing_gallery_bytes,
    sync_gallery_with_image_files,
)
from ..repositories.settings import sync_overall_config_env_values
from ..repositories.thumbnail_jobs import cleanup_auxiliary_state


logger = logging.getLogger(__name__)
FRONTEND_BUILD_DIR = config.PROJECT_ROOT / "frontend" / "build"
MAX_GENERATE_JOBS = 100
GENERATE_JOB_PERSIST_INTERVAL_SECONDS = 5.0
GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS = 0.35
STARTUP_MAINTENANCE_LEASE_SECONDS = 600
STARTUP_MAINTENANCE_COMPLETED_TTL_SECONDS = 600


def _lease_expires_at(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, seconds))).isoformat()


def cleanup_stale_edit_source_files():
    temp_dir = Path(config.DATA_DIR) / "edit-sources"
    if not temp_dir.exists():
        return

    removed = 0
    for temp_path in temp_dir.glob("edit-source-*"):
        if not temp_path.is_file():
            continue
        try:
            temp_path.unlink()
            removed += 1
        except OSError:
            logger.warning("Failed to remove stale edit source temp file: %s", temp_path)
    if removed:
        logger.info("Removed %s stale edit source temp file(s)", removed)


def cleanup_stale_gallery_export_files():
    temp_dir = Path(config.DATA_DIR) / "exports"
    if not temp_dir.exists():
        return

    try:
        known_export_ids = list_gallery_job_ids_with_files("export")
    except Exception:
        logger.warning("Failed to load gallery export job records before temp cleanup", exc_info=True)
        return

    removed = 0
    for temp_path in temp_dir.glob("*.zip"):
        if not temp_path.is_file():
            continue
        if temp_path.stem in known_export_ids:
            continue
        try:
            temp_path.unlink()
            removed += 1
        except OSError:
            logger.warning("Failed to remove stale gallery export temp file: %s", temp_path)
    if removed:
        logger.info("Removed %s stale gallery export temp file(s)", removed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from . import presets
    from ..services import job_events, job_scheduler

    app.state.worker_id = f"{os.getpid()}-{id(app)}"
    secrets.configure_registry(config.SECRET_REGISTRY_JSON)
    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.THUMBNAILS_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    rows = sync_overall_config_env_values(overall_config.current_env_snapshot())
    overall_config.apply_rows_to_config(
        rows,
        include_restart_required=True,
        overrides_only=True,
    )

    if not config.ACCESS_KEY and not config.ALLOW_UNAUTHENTICATED:
        raise RuntimeError(
            "ACCESS_KEY is required. Set ACCESS_KEY, or set "
            "ALLOW_UNAUTHENTICATED=true to explicitly run without authentication."
        )
    admin_key = auth.configured_admin_key()
    if not admin_key and not (
        config.ALLOW_UNAUTHENTICATED and not config.ACCESS_KEY
    ):
        raise RuntimeError("ADMIN_KEY is required for Settings management step-up")
    if (config.PUBLIC_IMAGE_BASE_URL or config.PUBLIC_THUMBNAIL_BASE_URL) and len(
        config.CDN_SIGNING_SECRET.encode("utf-8")
    ) < 32:
        raise RuntimeError(
            "Public CDN media URLs require CDN_SIGNING_SECRET with at least 32 bytes"
        )
    if config.ALLOW_UNAUTHENTICATED and not config.ACCESS_KEY:
        logger.warning(
            "ALLOW_UNAUTHENTICATED=true and ACCESS_KEY is unset; all non-health API "
            "routes are running without access-key authentication."
        )
        if not admin_key:
            logger.warning(
                "ADMIN_KEY is unset; Settings management step-up is not enforced "
                "in unauthenticated mode."
            )

    auth.validate_proxy_config()

    startup_maintenance_owner = f"startup-maintenance:{app.state.worker_id}"
    run_startup_maintenance = await asyncio.to_thread(
        acquire_background_lease,
        name="startup_maintenance",
        owner=startup_maintenance_owner,
        lease_expires_at=_lease_expires_at(STARTUP_MAINTENANCE_LEASE_SECONDS),
        completed_ttl_seconds=STARTUP_MAINTENANCE_COMPLETED_TTL_SECONDS,
    )
    if not run_startup_maintenance:
        verify_storage_writable()
        logger.info("Skipping startup maintenance; another worker already owns it")
    else:
        try:
            cleanup_stale_edit_source_files()
            cleanup_stale_gallery_export_files()
            verify_storage_writable()
        except Exception:
            release_background_lease(
                name="startup_maintenance",
                owner=startup_maintenance_owner,
            )
            raise

    logger.info("Image jobs resume through SQLite unit leases")
    if run_startup_maintenance:
        try:
            removed_gallery_entries = sync_gallery_with_image_files()
            cleaned_auxiliary = cleanup_auxiliary_state()
            if removed_gallery_entries:
                logger.info(
                    "Removed %s stale gallery entries for missing image files",
                    removed_gallery_entries,
                )
            if any(cleaned_auxiliary.values()):
                logger.info("Cleaned stale auxiliary rows: %s", cleaned_auxiliary)
        except Exception:
            release_background_lease(
                name="startup_maintenance",
                owner=startup_maintenance_owner,
            )
            raise

    async def _background_backfill_gallery_bytes():
        await asyncio.sleep(1.0)
        try:
            updated = await asyncio.to_thread(backfill_missing_gallery_bytes)
            if updated:
                logger.info(
                    "Backfilled byte sizes for %s legacy gallery entry record(s)",
                    updated,
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("Failed to backfill legacy gallery byte sizes", exc_info=True)
        finally:
            await asyncio.to_thread(
                complete_background_lease,
                name="startup_maintenance",
                owner=startup_maintenance_owner,
            )

    app.state._backfill_task = (
        asyncio.create_task(_background_backfill_gallery_bytes())
        if run_startup_maintenance
        else None
    )
    presets.load_api_settings()
    presets.validate_configured_secret_bindings()
    app.state.generate_jobs = {}
    app.state.generate_job_tasks = {}
    app.state.upstream_request_semaphore = asyncio.Semaphore(config.MAX_ACTIVE_GENERATE_JOBS)
    app.state.generate_job_subscribers = {}
    app.state.generate_jobs_subscribers = set()
    app.state.generate_jobs_broadcast_task = None
    app.state.generate_jobs_broadcast_reconcile = False
    app.state.generate_jobs_sse_poller_task = None
    app.state.generate_job_sse_poller_task = None
    app.state.generate_job_last_persist_at = {}
    app.state.image_unit_dispatcher_kick = asyncio.Event()
    app.state.image_unit_dispatcher_task = asyncio.create_task(
        job_scheduler.run_image_unit_dispatcher(app.state.worker_id)
    )
    app.state.gallery_export_lock = asyncio.Lock()
    app.state.gallery_job_subscribers = {"export": {}, "export_direct": {}, "sync": {}, "import": {}, "ai_analyze": {}}
    app.state.gallery_job_sse_poller_tasks = {}
    from ..services import gallery_jobs, gallery_maintenance
    from ..services import assistant_batch
    app.state.thumbnail_dispatcher_kick = asyncio.Event()
    app.state.thumbnail_dispatcher_task = asyncio.create_task(
        gallery_maintenance.run_thumbnail_dispatcher(app.state.worker_id)
    )
    app.state.gallery_export_dispatcher_task = asyncio.create_task(
        gallery_jobs.run_gallery_export_dispatcher(app.state.worker_id)
    )
    app.state.gallery_sync_dispatcher_task = asyncio.create_task(
        gallery_jobs.run_gallery_sync_dispatcher(app.state.worker_id)
    )
    app.state.gallery_import_dispatcher_task = asyncio.create_task(
        gallery_jobs.run_gallery_import_dispatcher(app.state.worker_id)
    )
    app.state.gallery_export_gc_task = asyncio.create_task(
        gallery_maintenance.gc_gallery_export_jobs(app.state.worker_id)
    )
    app.state.gallery_file_gc_task = asyncio.create_task(
        gallery_maintenance.run_gallery_file_gc(app.state.worker_id)
    )
    app.state.gallery_r2_scheduled_sync_task = asyncio.create_task(
        gallery_maintenance.run_gallery_r2_scheduled_sync(app.state.worker_id)
    )
    app.state.gallery_ai_analyze_dispatcher_task = asyncio.create_task(
        assistant_batch.run_ai_analyze_dispatcher(app.state.worker_id)
    )
    app.state.access_failures: OrderedDict[str, tuple[int, float]] = OrderedDict()
    job_events.reconcile_active_generate_jobs_from_storage()
    try:
        yield
    finally:
        backfill_task = getattr(app.state, "_backfill_task", None)
        if backfill_task and not backfill_task.done():
            backfill_task.cancel()
        broadcast_task = getattr(app.state, "generate_jobs_broadcast_task", None)
        if broadcast_task and not broadcast_task.done():
            broadcast_task.cancel()
        generate_jobs_sse_poller_task = getattr(
            app.state,
            "generate_jobs_sse_poller_task",
            None,
        )
        if generate_jobs_sse_poller_task and not generate_jobs_sse_poller_task.done():
            generate_jobs_sse_poller_task.cancel()
        generate_job_sse_poller_task = getattr(
            app.state,
            "generate_job_sse_poller_task",
            None,
        )
        if generate_job_sse_poller_task and not generate_job_sse_poller_task.done():
            generate_job_sse_poller_task.cancel()
        dispatcher_task = getattr(app.state, "image_unit_dispatcher_task", None)
        if dispatcher_task and not dispatcher_task.done():
            dispatcher_task.cancel()
        gallery_export_dispatcher_task = getattr(app.state, "gallery_export_dispatcher_task", None)
        if gallery_export_dispatcher_task and not gallery_export_dispatcher_task.done():
            gallery_export_dispatcher_task.cancel()
        gallery_sync_dispatcher_task = getattr(app.state, "gallery_sync_dispatcher_task", None)
        if gallery_sync_dispatcher_task and not gallery_sync_dispatcher_task.done():
            gallery_sync_dispatcher_task.cancel()
        gallery_import_dispatcher_task = getattr(app.state, "gallery_import_dispatcher_task", None)
        if gallery_import_dispatcher_task and not gallery_import_dispatcher_task.done():
            gallery_import_dispatcher_task.cancel()
        thumbnail_dispatcher_task = getattr(app.state, "thumbnail_dispatcher_task", None)
        if thumbnail_dispatcher_task and not thumbnail_dispatcher_task.done():
            thumbnail_dispatcher_task.cancel()
        gc_task = getattr(app.state, "gallery_export_gc_task", None)
        if gc_task and not gc_task.done():
            gc_task.cancel()
        file_gc_task = getattr(app.state, "gallery_file_gc_task", None)
        if file_gc_task and not file_gc_task.done():
            file_gc_task.cancel()
        scheduled_sync_task = getattr(app.state, "gallery_r2_scheduled_sync_task", None)
        if scheduled_sync_task and not scheduled_sync_task.done():
            scheduled_sync_task.cancel()
        ai_analyze_dispatcher_task = getattr(app.state, "gallery_ai_analyze_dispatcher_task", None)
        if ai_analyze_dispatcher_task and not ai_analyze_dispatcher_task.done():
            ai_analyze_dispatcher_task.cancel()
        gallery_job_sse_poller_tasks = list(
            getattr(app.state, "gallery_job_sse_poller_tasks", {}).values()
        )
        for task in gallery_job_sse_poller_tasks:
            if task and not task.done():
                task.cancel()
        tasks = list(getattr(app.state, "generate_job_tasks", {}).values())
        for task in tasks:
            task.cancel()
        awaitables = [
            task
            for task in (
                backfill_task,
                broadcast_task,
                generate_jobs_sse_poller_task,
                generate_job_sse_poller_task,
                dispatcher_task,
                thumbnail_dispatcher_task,
                gallery_export_dispatcher_task,
                gallery_sync_dispatcher_task,
                gallery_import_dispatcher_task,
                gc_task,
                file_gc_task,
                scheduled_sync_task,
                ai_analyze_dispatcher_task,
                *gallery_job_sse_poller_tasks,
                *tasks,
            )
            if task
        ]
        if awaitables:
            await asyncio.gather(*awaitables, return_exceptions=True)
        from ..integrations.session_pool import close_pool
        await close_pool()
        close_database_connections()


app = FastAPI(title="GPT Image Panel", lifespan=lifespan)
