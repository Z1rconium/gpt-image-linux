import logging
import mimetypes
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Iterable

from ...core import settings as config
from ...core.utils import utc_now
from ...core.validators import (
    get_env_var_ref_name,
    is_malformed_env_var_ref,
    normalize_r2_endpoint_url,
    resolve_env_var_ref,
)
from ...repositories.image_files import safe_image_path


HealthStatus = str
ProgressCallback = Callable[[dict[str, Any]], None]
ClientFactory = Callable[["R2EffectiveSettings"], Any]
SyncStateRecorder = Callable[[Iterable[dict[str, Any]]], None]
from .config import *

def _normalize_concurrency(value: Any | None = None) -> int:
    try:
        parsed = int(value if value is not None else config.R2_SYNC_CONCURRENCY)
    except (TypeError, ValueError):
        parsed = 4
    return max(1, min(parsed, 32))


def _build_s3_client(
    effective: R2EffectiveSettings,
    *,
    max_pool_connections: int | None = None,
):
    try:
        import boto3
        from botocore.config import Config as BotocoreConfig
    except ImportError as e:
        raise RuntimeError(
            "boto3 is not installed. Install backend requirements before using R2 sync."
        ) from e

    return boto3.client(
        "s3",
        endpoint_url=effective.endpoint_url,
        region_name=effective.region,
        aws_access_key_id=effective.access_key_id,
        aws_secret_access_key=effective.secret_access_key,
        config=BotocoreConfig(
            max_pool_connections=_normalize_concurrency(max_pool_connections),
        ),
    )


def _client_for(
    effective: R2EffectiveSettings,
    client_factory: ClientFactory | None,
    *,
    max_pool_connections: int | None = None,
):
    if client_factory:
        return client_factory(effective)
    return _build_s3_client(effective, max_pool_connections=max_pool_connections)


def probe_r2_settings(
    settings: dict[str, Any] | None,
    *,
    client_factory: ClientFactory | None = None,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []
    try:
        effective = resolve_r2_backup_settings(settings, require_enabled=False)
    except Exception as e:
        _check(checks, "configuration", "error", str(e))
        return {"status": _health_status(checks), "checks": checks}

    _check(checks, "configuration", "ok", "R2 configuration is complete")
    try:
        client = _client_for(effective, client_factory)
    except Exception as e:
        _check(checks, "client", "error", f"Failed to create S3 client: {e}")
        return {"status": _health_status(checks), "checks": checks}

    try:
        client.head_bucket(Bucket=effective.bucket_name)
        _check(checks, "head_bucket", "ok", "Bucket exists and credentials can access it")
    except Exception as e:
        _check(checks, "head_bucket", "error", f"HeadBucket failed: {e}")
        return {"status": _health_status(checks), "checks": checks}

    try:
        client.list_objects_v2(
            Bucket=effective.bucket_name,
            Prefix=effective.key_prefix,
            MaxKeys=1,
        )
        _check(checks, "list_prefix", "ok", "Prefix-scoped object listing succeeded")
    except Exception as e:
        _check(checks, "list_prefix", "error", f"ListObjectsV2 failed: {e}")
        return {"status": _health_status(checks), "checks": checks}

    probe_key = f"{effective.key_prefix}.r2-sync-probe-{uuid.uuid4().hex}.txt"
    try:
        client.put_object(
            Bucket=effective.bucket_name,
            Key=probe_key,
            Body=b"ok",
            ContentType="text/plain",
        )
        _check(checks, "write_probe", "ok", "Probe object write succeeded")
    except Exception as e:
        _check(checks, "write_probe", "error", f"Probe object write failed: {e}")
        return {"status": _health_status(checks), "checks": checks}

    try:
        client.delete_object(Bucket=effective.bucket_name, Key=probe_key)
        _check(checks, "delete_probe", "ok", "Probe object cleanup succeeded")
    except Exception as e:
        _check(
            checks,
            "delete_probe",
            "warning",
            f"Probe object was written but cleanup failed: {e}",
        )

    return {"status": _health_status(checks), "checks": checks}



__all__ = [name for name in globals() if not name.startswith("__")]

