import mimetypes
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..core.validators import (
    get_env_var_ref_name,
    is_malformed_env_var_ref,
    normalize_r2_endpoint_url,
    resolve_env_var_ref,
)
from ..repositories import storage


HealthStatus = str
ProgressCallback = Callable[[dict[str, Any]], None]
ClientFactory = Callable[["R2EffectiveSettings"], Any]


HEALTH_STATUS_RANK = {"ok": 0, "warning": 1, "error": 2}


class R2ConfigurationError(ValueError):
    pass


class R2SyncError(RuntimeError):
    def __init__(self, message: str, result: "R2SyncResult"):
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class R2EffectiveSettings:
    enabled: bool
    endpoint_url: str
    bucket_name: str
    region: str
    key_prefix: str
    access_key_id: str
    secret_access_key: str


@dataclass
class R2SyncResult:
    total_count: int = 0
    compared_count: int = 0
    uploaded_count: int = 0
    skipped_existing_count: int = 0
    missing_local_count: int = 0
    failed_count: int = 0
    bytes_total: int = 0
    bytes_uploaded: int = 0

    def to_updates(self) -> dict[str, int]:
        return asdict(self)


def _health_status(checks: list[dict[str, str]]) -> HealthStatus:
    if not checks:
        return "error"
    return max(
        (check["status"] for check in checks),
        key=lambda status: HEALTH_STATUS_RANK.get(status, 2),
    )


def _check(
    checks: list[dict[str, str]],
    name: str,
    status: HealthStatus,
    message: str,
) -> None:
    checks.append({"name": name, "status": status, "message": message})


def normalize_key_prefix(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parts = [part for part in raw.strip("/").split("/") if part]
    return f"{'/'.join(parts)}/" if parts else ""


def _resolve_secret(value: Any, field_name: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if is_malformed_env_var_ref(raw):
        raise R2ConfigurationError(
            f"{field_name} env ref must be formatted as ${{ENV_VAR_NAME}}."
        )
    env_var = get_env_var_ref_name(raw)
    resolved = resolve_env_var_ref(raw)
    if env_var and not resolved:
        raise R2ConfigurationError(
            f"{field_name} environment variable {env_var} is not set or empty."
        )
    return resolved


def resolve_r2_backup_settings(
    settings: dict[str, Any] | None,
    *,
    require_enabled: bool = False,
) -> R2EffectiveSettings:
    raw = settings or {}
    enabled = bool(raw.get("enabled", False))
    if require_enabled and not enabled:
        raise R2ConfigurationError("R2 backup is disabled.")

    endpoint_url = normalize_r2_endpoint_url(raw.get("endpoint_url"))
    if not endpoint_url:
        raise R2ConfigurationError("R2 endpoint URL is not configured.")

    bucket_name = str(raw.get("bucket_name") or "").strip()
    if not bucket_name:
        raise R2ConfigurationError("R2 bucket name is not configured.")

    region = str(raw.get("region") or "auto").strip() or "auto"
    key_prefix = normalize_key_prefix(raw.get("key_prefix"))
    access_key_id = _resolve_secret(raw.get("access_key_id"), "R2 access key ID")
    secret_access_key = _resolve_secret(
        raw.get("secret_access_key"),
        "R2 secret access key",
    )
    if not access_key_id:
        raise R2ConfigurationError("R2 access key ID is not configured.")
    if not secret_access_key:
        raise R2ConfigurationError("R2 secret access key is not configured.")

    return R2EffectiveSettings(
        enabled=enabled,
        endpoint_url=endpoint_url,
        bucket_name=bucket_name,
        region=region,
        key_prefix=key_prefix,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
    )


def _build_s3_client(effective: R2EffectiveSettings):
    try:
        import boto3
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
    )


def _client_for(
    effective: R2EffectiveSettings,
    client_factory: ClientFactory | None,
):
    return client_factory(effective) if client_factory else _build_s3_client(effective)


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


def _entry_value(entry: Any, key: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


def _entry_int(entry: Any, key: str, default: int = 0) -> int:
    try:
        value = int(_entry_value(entry, key) or default)
    except (TypeError, ValueError):
        return default
    return max(0, value)


def _list_remote_keys(client: Any, bucket_name: str, key_prefix: str) -> set[str]:
    paginator = client.get_paginator("list_objects_v2")
    keys: set[str] = set()
    for page in paginator.paginate(Bucket=bucket_name, Prefix=key_prefix):
        for item in page.get("Contents", []) or []:
            key = str(item.get("Key") or "")
            if key:
                keys.add(key)
    return keys


def _content_type_for(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _metadata_for_entry(entry: Any, byte_size: int) -> dict[str, str]:
    metadata: dict[str, str] = {}
    gallery_id = str(_entry_value(entry, "id") or "").strip()
    sha256 = str(_entry_value(entry, "sha256") or "").strip()
    if gallery_id:
        metadata["gallery-id"] = gallery_id
    if sha256:
        metadata["sha256"] = sha256
    if byte_size > 0:
        metadata["bytes"] = str(byte_size)
    return metadata


def sync_gallery_to_r2(
    settings: dict[str, Any] | None,
    entries: Iterable[Any],
    *,
    total_count: int = 0,
    progress_cb: ProgressCallback | None = None,
    client_factory: ClientFactory | None = None,
) -> R2SyncResult:
    effective = resolve_r2_backup_settings(settings, require_enabled=True)
    client = _client_for(effective, client_factory)
    result = R2SyncResult(total_count=max(0, int(total_count or 0)))
    errors: list[str] = []

    def publish(stage: str, message: str) -> None:
        if not progress_cb:
            return
        progress = 100 if result.total_count <= 0 else round(
            min(result.compared_count, result.total_count) / result.total_count * 100
        )
        progress_cb(
            {
                "stage": stage,
                "message": message,
                "progress": progress,
                **result.to_updates(),
            }
        )

    publish("listing_remote", "Listing existing R2 objects")
    remote_keys = _list_remote_keys(client, effective.bucket_name, effective.key_prefix)
    publish("comparing", "Comparing local gallery with R2 objects")

    for entry in entries:
        filename = str(_entry_value(entry, "filename") or "").strip()
        if not filename:
            result.missing_local_count += 1
            result.compared_count += 1
            publish("comparing", "Skipped a gallery row without filename")
            continue

        path = storage.safe_image_path(filename)
        if not path or not path.exists() or not path.is_file():
            result.missing_local_count += 1
            result.compared_count += 1
            publish("comparing", f"Skipped missing local file {filename}")
            continue

        byte_size = _entry_int(entry, "bytes") or path.stat().st_size
        result.bytes_total += byte_size
        key = f"{effective.key_prefix}{filename}"
        if key in remote_keys:
            result.skipped_existing_count += 1
            result.compared_count += 1
            publish("comparing", f"Skipped existing object {key}")
            continue

        extra_args = {
            "ContentType": _content_type_for(path),
            "Metadata": _metadata_for_entry(entry, byte_size),
        }
        try:
            client.upload_file(
                str(path),
                effective.bucket_name,
                key,
                ExtraArgs=extra_args,
            )
            remote_keys.add(key)
            result.uploaded_count += 1
            result.bytes_uploaded += byte_size
        except Exception as e:
            result.failed_count += 1
            errors.append(f"{filename}: {e}")
        finally:
            result.compared_count += 1
            publish("uploading", f"Compared {result.compared_count} gallery image(s)")

    if result.failed_count:
        sample = "; ".join(errors[:3])
        more = "" if len(errors) <= 3 else f"; {len(errors) - 3} more"
        raise R2SyncError(
            f"R2 sync failed for {result.failed_count} image(s): {sample}{more}",
            result,
        )

    publish("completed", "R2 sync completed")
    return result
