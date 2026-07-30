"""Bounded executors and memory admission for blocking backend work."""

import asyncio
import contextvars
import random
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, TypeVar

from ..core import settings as config
from ..core.observability import metrics


T = TypeVar("T")


class _BoundedExecutor:
    def __init__(self, name: str, max_workers: Callable[[], int]):
        self.name = name
        self._max_workers = max_workers
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._worker_count = 0
        self._pending = 0
        self._running = 0

    def _get_executor(self) -> ThreadPoolExecutor:
        with self._lock:
            if self._executor is None:
                self._worker_count = max(1, int(self._max_workers()))
                self._executor = ThreadPoolExecutor(
                    max_workers=self._worker_count,
                    thread_name_prefix=f"gpt-{self.name}",
                )
            return self._executor

    async def run(self, callback: Callable[[], T], *, metric_name: str) -> T:
        submitted_at = time.perf_counter()
        context = contextvars.copy_context()
        with self._lock:
            self._pending += 1

        def invoke() -> T:
            started_at = time.perf_counter()
            with self._lock:
                self._pending -= 1
                self._running += 1
            metrics.observe_ms(
                f"executor.{self.name}.queue_wait",
                (started_at - submitted_at) * 1000,
            )
            try:
                return context.run(callback)
            finally:
                metrics.observe_ms(
                    metric_name,
                    (time.perf_counter() - started_at) * 1000,
                )
                with self._lock:
                    self._running -= 1

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(self._get_executor(), invoke)
        except asyncio.CancelledError:
            # The worker may still be finishing; counters are finalized there.
            raise

    def gauges(self) -> dict[str, int]:
        with self._lock:
            return {
                f"executor.{self.name}.queued": self._pending,
                f"executor.{self.name}.running": self._running,
                f"executor.{self.name}.capacity": self._worker_count
                or max(1, int(self._max_workers())),
            }

    def shutdown(self) -> None:
        with self._lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._pending = 0
            self._running = 0
            self._worker_count = 0

    def run_on_each_worker(self, callback: Callable[[], None]) -> None:
        with self._lock:
            executor = self._executor
            worker_count = self._worker_count
        if executor is None:
            return
        if worker_count <= 0:
            return
        barrier = threading.Barrier(worker_count)

        def invoke() -> None:
            barrier.wait()
            callback()

        futures = [executor.submit(invoke) for _ in range(worker_count)]
        for future in futures:
            future.result()


_db_executor = _BoundedExecutor("db", lambda: config.DB_EXECUTOR_WORKERS)
_image_executor = _BoundedExecutor("image_cpu", lambda: config.IMAGE_CPU_CONCURRENCY)
_file_executor = _BoundedExecutor("file_io", lambda: config.FILE_IO_CONCURRENCY)


def _is_sqlite_busy(error: BaseException) -> bool:
    return isinstance(error, sqlite3.OperationalError) and any(
        marker in str(error).lower() for marker in ("locked", "busy")
    )


async def run_db_operation(
    callback: Callable[..., T],
    *args: Any,
    metric_name: str | None = None,
    retry_busy: bool = True,
    **kwargs: Any,
) -> T:
    """Run one repository operation with a short lock timeout and jittered retry."""

    from ..repositories import db as db_repo

    label = metric_name or getattr(callback, "__name__", "operation")

    def invoke() -> T:
        with db_repo.persistent_connection_scope(config.SQLITE_BUSY_TIMEOUT_MS):
            return callback(*args, **kwargs)

    attempts = config.SQLITE_BUSY_RETRY_ATTEMPTS if retry_busy else 0
    for attempt in range(attempts + 1):
        try:
            return await _db_executor.run(invoke, metric_name=f"db.{label}")
        except BaseException as error:
            if not _is_sqlite_busy(error) or attempt >= attempts:
                raise
            metrics.increment("sqlite.busy_retries")
            base = config.SQLITE_BUSY_RETRY_BASE_MS / 1000
            await asyncio.sleep(base * (2**attempt) * random.uniform(0.75, 1.25))
    raise RuntimeError("unreachable")


async def run_image_operation(
    callback: Callable[..., T],
    *args: Any,
    metric_name: str | None = None,
    **kwargs: Any,
) -> T:
    label = metric_name or getattr(callback, "__name__", "operation")
    return await _image_executor.run(
        partial(callback, *args, **kwargs),
        metric_name=f"image_cpu.{label}",
    )


async def run_file_operation(
    callback: Callable[..., T],
    *args: Any,
    metric_name: str | None = None,
    **kwargs: Any,
) -> T:
    label = metric_name or getattr(callback, "__name__", "operation")
    return await _file_executor.run(
        partial(callback, *args, **kwargs),
        metric_name=f"file_io.{label}",
    )


def executor_gauges() -> dict[str, int]:
    gauges: dict[str, int] = {}
    for executor in (_db_executor, _image_executor, _file_executor):
        gauges.update(executor.gauges())
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        budget = None
    else:
        budget = getattr(loop, "_gpt_upstream_memory_budget", None)
    gauges["upstream.memory_budget_capacity_bytes"] = (
        budget.capacity if budget else config.UPSTREAM_MEMORY_BUDGET_MB * 1024 * 1024
    )
    gauges["upstream.memory_budget_used_bytes"] = (
        budget.capacity - budget.available if budget else 0
    )
    return gauges


class _WeightedMemoryBudget:
    def __init__(self, capacity: int):
        self.capacity = max(1, int(capacity))
        self.available = self.capacity
        self.condition = asyncio.Condition()

    async def acquire(self, requested: int) -> int:
        weight = min(self.capacity, max(1, int(requested)))
        started_at = time.perf_counter()
        async with self.condition:
            while self.available < weight:
                await self.condition.wait()
            self.available -= weight
        metrics.observe_ms(
            "upstream.memory_budget_wait",
            (time.perf_counter() - started_at) * 1000,
        )
        return weight

    async def release(self, weight: int) -> None:
        async with self.condition:
            self.available = min(self.capacity, self.available + max(0, weight))
            self.condition.notify_all()


def _get_memory_budget() -> _WeightedMemoryBudget:
    loop = asyncio.get_running_loop()
    capacity = config.UPSTREAM_MEMORY_BUDGET_MB * 1024 * 1024
    budget = getattr(loop, "_gpt_upstream_memory_budget", None)
    if budget is None or budget.capacity != capacity:
        budget = _WeightedMemoryBudget(capacity)
        setattr(loop, "_gpt_upstream_memory_budget", budget)
    return budget


@asynccontextmanager
async def upstream_memory_lease(expected_bytes: int):
    budget = _get_memory_budget()
    weight = await budget.acquire(expected_bytes)
    try:
        yield
    finally:
        await budget.release(weight)


async def close_blocking_executors() -> None:
    from ..repositories import db as db_repo

    _db_executor.run_on_each_worker(db_repo._close_thread_connection)
    for executor in (_file_executor, _image_executor, _db_executor):
        executor.shutdown()


__all__ = [
    "close_blocking_executors",
    "executor_gauges",
    "run_db_operation",
    "run_file_operation",
    "run_image_operation",
    "upstream_memory_lease",
]
