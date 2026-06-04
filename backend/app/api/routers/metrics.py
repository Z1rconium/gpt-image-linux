from fastapi import APIRouter, HTTPException, Request, Response

from ..app_state import app
from ..jobs import snapshot_queue_metrics
from ...core import settings as config
from ...core.observability import build_metrics_snapshot, format_prometheus_metrics
from ...repositories import storage


router = APIRouter()


def _failure_rates(counters: dict) -> dict[str, float]:
    rates: dict[str, float] = {}
    for operation in ("generation", "edit"):
        failed = int(counters.get(f"image_jobs.{operation}.failed", 0))
        succeeded = int(counters.get(f"image_jobs.{operation}.succeeded", 0))
        total = failed + succeeded
        rates[f"image_jobs.{operation}.failure_ratio"] = failed / total if total else 0.0
    return rates


def _metrics_snapshot() -> dict:
    worker_id = str(getattr(app.state, "worker_id", "unknown"))
    runtime = storage.get_runtime_coordination_metrics()
    gauges = snapshot_queue_metrics()
    gauges.update(runtime.get("gauges", {}))
    snapshot = build_metrics_snapshot(gauges=gauges)
    snapshot["rates"] = _failure_rates(snapshot["counters"])
    snapshot["worker_id"] = worker_id
    snapshot["background_leases"] = runtime.get("background_leases", [])
    storage.record_worker_metrics_snapshot(
        worker_id,
        {
            "counters": snapshot["counters"],
            "gauges": snapshot["gauges"],
            "rates": snapshot["rates"],
            "timings_ms": snapshot["timings_ms"],
        },
    )
    snapshot["workers"] = storage.get_runtime_coordination_metrics().get("workers", [])
    return snapshot


def _ensure_metrics_enabled():
    if not config.ENABLE_METRICS:
        raise HTTPException(status_code=404, detail="Metrics endpoint is disabled")


@router.get("/api/metrics")
async def get_metrics(request: Request):
    _ensure_metrics_enabled()
    snapshot = _metrics_snapshot()
    if "text/plain" in request.headers.get("accept", ""):
        return Response(
            format_prometheus_metrics(snapshot),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    return {"enabled": True, **snapshot}


@router.get("/api/metrics/prometheus")
async def get_prometheus_metrics():
    _ensure_metrics_enabled()
    return Response(
        format_prometheus_metrics(_metrics_snapshot()),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
