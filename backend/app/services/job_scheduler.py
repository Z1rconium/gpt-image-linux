"""SQLite image-unit dispatcher and worker heartbeat loop."""

import asyncio
import logging
import time

from ..core import settings as config
from ..core.observability import metrics
from ..core.utils import utc_now
from ..repositories.coordination import mark_worker_heartbeat
from ..repositories.image_jobs import claim_next_image_job_unit
from .job_executor import image_unit_lease_expires_at, run_claimed_image_unit
from .job_queue import get_image_unit_dispatcher_kick_event

logger = logging.getLogger(__name__)

IMAGE_DISPATCHER_HEARTBEAT_INTERVAL_SECONDS = 5.0
IMAGE_DISPATCHER_MAX_IDLE_BACKOFF_SECONDS = 2.0
async def wait_for_image_dispatcher_wakeup(
    delay_seconds: float,
    active_tasks: set[asyncio.Task],
) -> bool:
    kick_event = get_image_unit_dispatcher_kick_event()
    kick_task = asyncio.create_task(kick_event.wait())
    wait_tasks = {*active_tasks, kick_task}
    done, _pending = await asyncio.wait(
        wait_tasks,
        timeout=max(0.0, delay_seconds),
        return_when=asyncio.FIRST_COMPLETED,
    )
    kicked = kick_task in done and kick_event.is_set()
    if kicked:
        kick_event.clear()
    if not kick_task.done():
        kick_task.cancel()
        await asyncio.gather(kick_task, return_exceptions=True)
    return kicked


async def run_image_unit_dispatcher(worker_id: str):
    logger.info("Image unit dispatcher started: worker_id=%s", worker_id)
    active_tasks: set[asyncio.Task] = set()
    last_heartbeat_at = 0.0
    last_heartbeat_active_units: int | None = None
    idle_delay = config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS

    def heartbeat_if_needed(*, force: bool = False):
        nonlocal last_heartbeat_active_units, last_heartbeat_at
        active_units = len(active_tasks)
        now = time.monotonic()
        if (
            force
            or active_units != last_heartbeat_active_units
            or now - last_heartbeat_at >= IMAGE_DISPATCHER_HEARTBEAT_INTERVAL_SECONDS
        ):
            mark_worker_heartbeat(worker_id, active_units)
            last_heartbeat_active_units = active_units
            last_heartbeat_at = now

    try:
        while True:
            active_tasks = {task for task in active_tasks if not task.done()}
            heartbeat_if_needed()
            claimed_count = 0
            while len(active_tasks) < config.MAX_ACTIVE_GENERATE_JOBS:
                unit = claim_next_image_job_unit(
                    worker_id=worker_id,
                    lease_expires_at=image_unit_lease_expires_at(),
                    now=utc_now(),
                    running_limit=config.MAX_ACTIVE_GENERATE_JOBS,
                )
                if not unit:
                    break
                task = asyncio.create_task(run_claimed_image_unit(unit, worker_id))
                active_tasks.add(task)
                claimed_count += 1
            if claimed_count > 0:
                heartbeat_if_needed(force=True)
                idle_delay = config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS
            elif len(active_tasks) < config.MAX_ACTIVE_GENERATE_JOBS:
                metrics.increment("image_jobs.claim_miss")
                idle_delay = min(
                    IMAGE_DISPATCHER_MAX_IDLE_BACKOFF_SECONDS,
                    max(config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS, idle_delay * 2),
                )

            if active_tasks:
                next_heartbeat_in = max(
                    0.0,
                    IMAGE_DISPATCHER_HEARTBEAT_INTERVAL_SECONDS
                    - (time.monotonic() - last_heartbeat_at),
                )
                sleep_for = min(idle_delay, next_heartbeat_in or idle_delay)
            else:
                sleep_for = idle_delay
            if await wait_for_image_dispatcher_wakeup(sleep_for, active_tasks):
                idle_delay = config.IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS
    except asyncio.CancelledError:
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        raise

