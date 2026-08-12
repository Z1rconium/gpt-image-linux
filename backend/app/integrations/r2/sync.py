import logging
import mimetypes
import uuid
from contextlib import nullcontext
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
from .client import *

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


def _is_not_found_error(error: Exception) -> bool:
    response = getattr(error, "response", {})
    error_info = response.get("Error", {}) if isinstance(response, dict) else {}
    code = str(error_info.get("Code") or "").lower()
    status = str(
        (response.get("ResponseMetadata", {}) if isinstance(response, dict) else {}).get(
            "HTTPStatusCode"
        )
        or ""
    )
    return code in {"404", "nosuchkey", "notfound"} or status == "404"


def _etag_from_response(response: Any) -> str | None:
    if not isinstance(response, dict):
        return None
    etag = str(response.get("ETag") or "").strip().strip('"')
    return etag or None


def _list_remote_keys(
    client: Any,
    bucket_name: str,
    key_prefix: str,
    *,
    candidate_keys: set[str],
    fallback_threshold: int = R2_REMOTE_LISTING_FALLBACK_THRESHOLD,
) -> RemoteKeyLookup:
    paginator = client.get_paginator("list_objects_v2")
    keys: set[str] = set()
    etags: dict[str, str] = {}
    scanned_count = 0
    for page in paginator.paginate(Bucket=bucket_name, Prefix=key_prefix):
        for item in page.get("Contents", []) or []:
            key = str(item.get("Key") or "")
            if not key:
                continue
            scanned_count += 1
            if key in candidate_keys:
                keys.add(key)
                etag = _etag_from_response(item)
                if etag:
                    etags[key] = etag
                if len(keys) == len(candidate_keys):
                    return RemoteKeyLookup(keys=keys, etags=etags)
            if scanned_count >= fallback_threshold:
                return RemoteKeyLookup(keys=keys, etags=etags, use_head_fallback=True)
    return RemoteKeyLookup(keys=keys, etags=etags)


def _head_candidate_key(
    client: Any,
    *,
    bucket_name: str,
    key: str,
) -> tuple[str, str | None] | None:
    try:
        response = client.head_object(Bucket=bucket_name, Key=key)
        return key, _etag_from_response(response)
    except Exception as e:
        if _is_not_found_error(e):
            return None
        raise


def _head_remote_keys(
    client: Any,
    bucket_name: str,
    candidate_keys: set[str],
    *,
    concurrency: int,
) -> RemoteKeyLookup:
    keys: set[str] = set()
    etags: dict[str, str] = {}
    if not candidate_keys:
        return RemoteKeyLookup(keys=keys)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(
                _head_candidate_key,
                client,
                bucket_name=bucket_name,
                key=key,
            ): key
            for key in candidate_keys
        }
        for future in as_completed(futures):
            found = future.result()
            if found:
                found_key, etag = found
                keys.add(found_key)
                if etag:
                    etags[found_key] = etag
    return RemoteKeyLookup(keys=keys, etags=etags)


def _complete_head_fallback(
    client: Any,
    bucket_name: str,
    lookup: RemoteKeyLookup,
    *,
    candidate_keys: set[str],
    concurrency: int,
) -> RemoteKeyLookup:
    if not lookup.use_head_fallback:
        return lookup
    unresolved_keys = candidate_keys - lookup.keys
    if unresolved_keys:
        resolved = _head_remote_keys(
            client,
            bucket_name,
            unresolved_keys,
            concurrency=concurrency,
        )
        lookup.keys.update(resolved.keys)
        lookup.etags.update(resolved.etags)
    lookup.use_head_fallback = False
    return lookup


def _remote_key_lookup_for_batch(
    client: Any,
    bucket_name: str,
    key_prefix: str,
    *,
    candidate_keys: set[str],
    concurrency: int,
    full_reconcile: bool,
) -> RemoteKeyLookup:
    if not candidate_keys:
        return RemoteKeyLookup(keys=set())
    if not full_reconcile or len(candidate_keys) <= R2_REMOTE_HEAD_LOOKUP_THRESHOLD:
        return _head_remote_keys(
            client,
            bucket_name,
            candidate_keys,
            concurrency=concurrency,
        )
    lookup = _list_remote_keys(
        client,
        bucket_name,
        key_prefix,
        candidate_keys=candidate_keys,
    )
    return _complete_head_fallback(
        client,
        bucket_name,
        lookup,
        candidate_keys=candidate_keys,
        concurrency=concurrency,
    )


def _local_sync_candidates_for_batch(
    effective: R2EffectiveSettings,
    entries: Iterable[Any],
) -> tuple[list[LocalSyncCandidate], R2SyncResult, set[str]]:
    candidates: list[LocalSyncCandidate] = []
    result = R2SyncResult()
    candidate_keys: set[str] = set()
    for entry in entries:
        filename = str(_entry_value(entry, "filename") or "").strip()
        if not filename:
            result.missing_local_count += 1
            result.compared_count += 1
            continue

        path = safe_image_path(filename)
        if not path or not path.exists() or not path.is_file():
            result.missing_local_count += 1
            result.compared_count += 1
            continue

        byte_size = _entry_int(entry, "bytes") or path.stat().st_size
        key = f"{effective.key_prefix}{filename}"
        candidates.append(
            LocalSyncCandidate(
                entry=entry,
                filename=filename,
                path=path,
                byte_size=byte_size,
                key=key,
            )
        )
        candidate_keys.add(key)
        result.bytes_total += byte_size
    return candidates, result, candidate_keys


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


def _batched_entries(entries: Iterable[Any], batch_size: int) -> Iterable[list[Any]]:
    iterator = iter(entries)
    normalized_batch_size = max(1, int(batch_size or R2_SYNC_BATCH_SIZE))
    while True:
        batch = list(islice(iterator, normalized_batch_size))
        if not batch:
            return
        yield batch


def _sync_candidate(
    client: Any,
    candidate: LocalSyncCandidate,
    *,
    bucket_name: str,
    remote_keys: set[str],
) -> CandidateSyncOutcome:
    if candidate.key in remote_keys:
        return CandidateSyncOutcome(candidate=candidate, skipped_existing=True)

    extra_args = {
        "ContentType": _content_type_for(candidate.path),
        "Metadata": _metadata_for_entry(candidate.entry, candidate.byte_size),
    }
    try:
        client.upload_file(
            str(candidate.path),
            bucket_name,
            candidate.key,
            ExtraArgs=extra_args,
        )
        return CandidateSyncOutcome(candidate=candidate, uploaded=True)
    except Exception as e:
        return CandidateSyncOutcome(candidate=candidate, error=str(e))


def _state_row_for_candidate(
    candidate: LocalSyncCandidate,
    *,
    etag: str | None = None,
) -> dict[str, Any]:
    return {
        "filename": candidate.filename,
        "sha256": str(_entry_value(candidate.entry, "sha256") or "").strip(),
        "bytes": candidate.byte_size,
        "key": candidate.key,
        "etag": etag,
        "last_remote_seen_at": utc_now(),
    }


def sync_gallery_to_r2(
    settings: dict[str, Any] | None,
    entries: Iterable[Any],
    *,
    total_count: int = 0,
    progress_cb: ProgressCallback | None = None,
    client_factory: ClientFactory | None = None,
    state_recorder: SyncStateRecorder | None = None,
    full_reconcile: bool = False,
    dry_run: bool = False,
    concurrency: int | None = None,
    batch_size: int = R2_SYNC_BATCH_SIZE,
) -> R2SyncResult:
    effective = resolve_r2_backup_settings(settings, require_enabled=True)
    normalized_concurrency = _normalize_concurrency(concurrency)
    client = _client_for(
        effective,
        client_factory,
        max_pool_connections=normalized_concurrency,
    )
    known_total_count = max(0, int(total_count or 0))
    result = R2SyncResult(total_count=known_total_count)
    errors: list[str] = []

    def publish(stage: str, message: str, *, last_filename: str | None = None) -> None:
        if not progress_cb:
            return
        progress = 100 if result.total_count <= 0 else round(
            min(result.compared_count, result.total_count) / result.total_count * 100
        )
        updates = {
            "stage": stage,
            "message": message,
            "progress": progress,
            **result.to_updates(),
        }
        if last_filename:
            updates["last_filename"] = last_filename
        progress_cb(updates)

    publish("preparing", "Preparing local gallery candidates")

    executor_context = (
        nullcontext(None)
        if dry_run
        else ThreadPoolExecutor(max_workers=normalized_concurrency)
    )
    with executor_context as executor:
        for entry_batch in _batched_entries(entries, batch_size):
            candidates, batch_result, candidate_keys = _local_sync_candidates_for_batch(
                effective,
                entry_batch,
            )
            result.missing_local_count += batch_result.missing_local_count
            result.compared_count += batch_result.compared_count
            result.bytes_total += batch_result.bytes_total
            if known_total_count <= 0:
                result.total_count += len(candidates) + batch_result.missing_local_count

            if not candidates:
                publish("comparing", f"Compared {result.compared_count} gallery image(s)")
                continue

            publish("listing_remote", "Checking existing R2 objects")
            remote_keys = _remote_key_lookup_for_batch(
                client,
                effective.bucket_name,
                effective.key_prefix,
                candidate_keys=candidate_keys,
                concurrency=normalized_concurrency,
                full_reconcile=full_reconcile,
            )
            publish("comparing", "Comparing local gallery with R2 objects")

            if dry_run:
                for candidate in candidates:
                    if candidate.key in remote_keys.keys:
                        result.skipped_existing_count += 1
                    else:
                        result.pending_upload_count += 1
                    result.compared_count += 1
                    publish("preflight", f"Compared {result.compared_count} gallery image(s)")
                publish(
                    "checkpoint",
                    f"Checkpoint after {candidates[-1].filename}",
                    last_filename=candidates[-1].filename,
                )
                continue

            confirmed_rows: list[dict[str, Any]] = []
            assert executor is not None
            futures = [
                executor.submit(
                    _sync_candidate,
                    client,
                    candidate,
                    bucket_name=effective.bucket_name,
                    remote_keys=remote_keys.keys,
                )
                for candidate in candidates
            ]
            for future in as_completed(futures):
                outcome = future.result()
                if outcome.skipped_existing:
                    result.skipped_existing_count += 1
                    confirmed_rows.append(
                        _state_row_for_candidate(
                            outcome.candidate,
                            etag=remote_keys.etags.get(outcome.candidate.key),
                        )
                    )
                elif outcome.uploaded:
                    remote_keys.keys.add(outcome.candidate.key)
                    result.uploaded_count += 1
                    result.bytes_uploaded += outcome.candidate.byte_size
                    confirmed_rows.append(_state_row_for_candidate(outcome.candidate))
                else:
                    result.failed_count += 1
                    errors.append(f"{outcome.candidate.filename}: {outcome.error}")

                result.compared_count += 1
                publish("uploading", f"Compared {result.compared_count} gallery image(s)")

            publish(
                "checkpoint",
                f"Checkpoint after {candidates[-1].filename}",
                last_filename=candidates[-1].filename,
            )

            if state_recorder and confirmed_rows:
                try:
                    state_recorder(confirmed_rows)
                except Exception:
                    logger.warning("Failed to record R2 sync state", exc_info=True)

    if result.failed_count:
        sample = "; ".join(errors[:3])
        more = "" if len(errors) <= 3 else f"; {len(errors) - 3} more"
        raise R2SyncError(
            f"R2 sync failed for {result.failed_count} image(s): {sample}{more}",
            result,
        )

    publish("completed", "R2 sync completed")
    return result
