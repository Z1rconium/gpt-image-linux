import asyncio
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ..core import security as auth
from ..core import settings as config
from ..repositories import storage


logger = logging.getLogger(__name__)
FRONTEND_BUILD_DIR = config.PROJECT_ROOT / "frontend" / "build"
MAX_GENERATE_JOBS = 100
GENERATE_JOB_PERSIST_INTERVAL_SECONDS = 5.0
GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS = 0.35


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

    removed = 0
    for temp_path in temp_dir.glob("*"):
        if not temp_path.is_file():
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
    from . import jobs, presets

    if not config.ACCESS_KEY and not config.ALLOW_UNAUTHENTICATED:
        raise RuntimeError(
            "ACCESS_KEY is required. Set ACCESS_KEY, or set "
            "ALLOW_UNAUTHENTICATED=true to explicitly run without authentication."
        )

    auth.validate_proxy_config()

    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.THUMBNAILS_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    cleanup_stale_edit_source_files()
    cleanup_stale_gallery_export_files()
    storage.verify_storage_writable()
    interrupted_jobs = storage.mark_active_generate_jobs_interrupted()
    if interrupted_jobs:
        logger.info("Marked %s interrupted generation job(s)", interrupted_jobs)
    removed_gallery_entries = storage.sync_gallery_with_image_files()
    if removed_gallery_entries:
        logger.info(
            "Removed %s stale gallery entries for missing image files",
            removed_gallery_entries,
        )
    try:
        backfilled_gallery_bytes = storage.backfill_missing_gallery_bytes()
    except Exception:
        logger.warning("Failed to backfill legacy gallery byte sizes", exc_info=True)
    else:
        if backfilled_gallery_bytes:
            logger.info(
                "Backfilled byte sizes for %s legacy gallery entry record(s)",
                backfilled_gallery_bytes,
            )
    presets.load_api_settings()
    app.state.generate_jobs = {}
    app.state.generate_job_tasks = {}
    app.state.generate_job_semaphore = asyncio.Semaphore(config.MAX_ACTIVE_GENERATE_JOBS)
    app.state.upstream_request_semaphore = asyncio.Semaphore(config.MAX_ACTIVE_GENERATE_JOBS)
    app.state.generate_job_subscribers = {}
    app.state.generate_jobs_subscribers = set()
    app.state.generate_jobs_broadcast_task = None
    app.state.generate_jobs_broadcast_reconcile = False
    app.state.generate_job_webhooks = {}
    app.state.generate_job_last_persist_at = {}
    app.state.gallery_export_jobs = {}
    app.state.gallery_export_tasks = {}
    app.state.gallery_export_subscribers = {}
    app.state.gallery_export_lock = asyncio.Lock()
    app.state.gallery_export_direct_downloads = 0
    from .routers.gallery import gc_gallery_export_jobs
    app.state.gallery_export_gc_task = asyncio.create_task(gc_gallery_export_jobs())
    app.state.pending_edit_source_bytes = 0
    app.state.access_failures: OrderedDict[str, tuple[int, float]] = OrderedDict()
    jobs.reconcile_active_generate_jobs_from_storage()
    try:
        yield
    finally:
        broadcast_task = app.state.generate_jobs_broadcast_task
        if broadcast_task and not broadcast_task.done():
            broadcast_task.cancel()
        gc_task = getattr(app.state, "gallery_export_gc_task", None)
        if gc_task and not gc_task.done():
            gc_task.cancel()
        tasks = list(jobs.get_generate_job_tasks().values())
        gallery_export_tasks = list(getattr(app.state, "gallery_export_tasks", {}).values())
        for task in gallery_export_tasks:
            task.cancel()
        for task in tasks:
            task.cancel()
        awaitables = [task for task in (broadcast_task, gc_task, *tasks, *gallery_export_tasks) if task]
        if awaitables:
            await asyncio.gather(*awaitables, return_exceptions=True)
        for job in getattr(app.state, "gallery_export_jobs", {}).values():
            path = job.get("path")
            if path:
                Path(path).unlink(missing_ok=True)
        from ..integrations.session_pool import close_pool
        await close_pool()
        storage.close_database_connections()


app = FastAPI(title="GPT Image Panel", lifespan=lifespan)
