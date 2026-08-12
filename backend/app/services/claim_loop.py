"""Shared async claim-loop helpers for SQLite-backed background dispatchers."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

NumberProvider = int | float | Callable[[], int | float]
Hook = Callable[..., Any]


def _resolve_number(value: NumberProvider) -> float:
    return float(value() if callable(value) else value)


async def _call_hook(hook: Hook | None, *args: Any) -> Any:
    if hook is None:
        return None
    result = hook(*args)
    if inspect.isawaitable(result):
        return await result
    return result


def _reap_completed_tasks(
    active_tasks: set[asyncio.Task],
    *,
    logger: logging.Logger,
    task_name: str,
) -> set[asyncio.Task]:
    remaining: set[asyncio.Task] = set()
    for task in active_tasks:
        if not task.done():
            remaining.add(task)
            continue
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("%s task failed", task_name, exc_info=True)
    return remaining


async def _wait_for_claim_loop_wakeup(
    delay_seconds: float,
    active_tasks: set[asyncio.Task],
    *,
    kick_event: asyncio.Event | None,
    wait_on_active_tasks: bool,
) -> bool:
    delay_seconds = max(0.0, float(delay_seconds))
    kick_task: asyncio.Task | None = None
    wait_tasks: set[asyncio.Task] = set()
    if kick_event is not None:
        kick_task = asyncio.create_task(kick_event.wait())
        wait_tasks.add(kick_task)
    if wait_on_active_tasks:
        wait_tasks.update(active_tasks)

    if not wait_tasks:
        await asyncio.sleep(delay_seconds)
        return False

    done, _pending = await asyncio.wait(
        wait_tasks,
        timeout=delay_seconds,
        return_when=asyncio.FIRST_COMPLETED,
    )
    kicked = kick_task in done and kick_event is not None and kick_event.is_set()
    if kicked:
        kick_event.clear()
    if kick_task is not None and not kick_task.done():
        kick_task.cancel()
        await asyncio.gather(kick_task, return_exceptions=True)
    return kicked


async def run_claim_loop(
    *,
    claim_fn: Callable[[], Awaitable[Any | None]],
    run_fn: Callable[[Any], Awaitable[Any]],
    running_limit: NumberProvider,
    idle_interval: NumberProvider,
    max_backoff: NumberProvider,
    kick_event: asyncio.Event | None = None,
    claim_miss_fn: Callable[[], Any] | None = None,
    before_cycle: Hook | None = None,
    after_claims: Hook | None = None,
    sleep_interval_fn: Callable[[set[asyncio.Task], float], float] | None = None,
    wait_on_active_tasks: bool = True,
    logger: logging.Logger | None = None,
    error_message: str = "Claim-loop dispatcher error",
    task_name: str = "claimed job",
) -> None:
    """Run a reusable claim -> task -> idle-backoff dispatcher loop."""

    log = logger or logging.getLogger(__name__)
    active_tasks: set[asyncio.Task] = set()
    idle_delay = max(0.0, _resolve_number(idle_interval))

    try:
        while True:
            try:
                active_tasks = _reap_completed_tasks(
                    active_tasks,
                    logger=log,
                    task_name=task_name,
                )
                await _call_hook(before_cycle, active_tasks)

                limit = max(1, int(_resolve_number(running_limit)))
                claimed_count = 0
                while len(active_tasks) < limit:
                    claimed = await claim_fn()
                    if not claimed:
                        break
                    active_tasks.add(asyncio.create_task(run_fn(claimed)))
                    claimed_count += 1

                if claimed_count:
                    idle_delay = max(0.0, _resolve_number(idle_interval))
                elif len(active_tasks) < limit:
                    if claim_miss_fn is not None:
                        claim_miss_fn()
                    idle_delay = min(
                        max(0.0, _resolve_number(max_backoff)),
                        max(max(0.0, _resolve_number(idle_interval)), idle_delay * 2),
                    )

                await _call_hook(after_claims, active_tasks, claimed_count)
                sleep_for = idle_delay
                if sleep_interval_fn is not None:
                    sleep_for = max(0.0, float(sleep_interval_fn(active_tasks, idle_delay)))
                kicked = await _wait_for_claim_loop_wakeup(
                    sleep_for,
                    active_tasks,
                    kick_event=kick_event,
                    wait_on_active_tasks=wait_on_active_tasks,
                )
                if kicked:
                    idle_delay = max(0.0, _resolve_number(idle_interval))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning(error_message, exc_info=True)
                await asyncio.sleep(max(0.0, _resolve_number(idle_interval)))
    except asyncio.CancelledError:
        for task in active_tasks:
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks, return_exceptions=True)
        raise
