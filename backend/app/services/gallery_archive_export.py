import json
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from collections.abc import Callable, Generator, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import HTTPException, UploadFile
from zipstream import ZipStream

from ..core import settings as config
from ..core.utils import utc_now
from ..repositories.image_files import safe_image_path, validate_image_bytes
from ..schemas.gallery import GalleryEntry
from ..api.uploads import IMAGE_UPLOAD_CONTENT_TYPES, IMAGE_UPLOAD_EXTENSIONS

GalleryZipProgressCallback = Callable[[dict[str, Any]], None]
from .gallery_archive_shared import *

def _entry_to_dict(entry: GalleryEntry | dict[str, Any]) -> dict[str, Any]:
    if isinstance(entry, dict):
        data = {
            key: entry.get(key)
            for key in _GALLERY_ENTRY_EXPORT_FIELDS
            if entry.get(key) is not None
        }
        for required in ("id", "prompt", "size", "filename", "created_at"):
            data.setdefault(required, entry.get(required, ""))
        data["favorite"] = bool(entry.get("favorite"))
        return data
    return entry.model_dump(exclude={"image_url", "thumbnail_filename", "thumbnail_url"})


def _entry_filename(entry: GalleryEntry | dict[str, Any]) -> str:
    if isinstance(entry, dict):
        return str(entry.get("filename") or "")
    return entry.filename


def _entry_sha256(entry: GalleryEntry | dict[str, Any]) -> str | None:
    if isinstance(entry, dict):
        value = entry.get("sha256")
        return str(value) if value else None
    return None


def _resolve_export_metadata_for_entry(
    entry: GalleryEntry | dict[str, Any],
    path: Path,
) -> dict[str, Any]:
    data = _entry_to_dict(entry)
    cached_hash = _entry_sha256(entry)
    cached_bytes = data.get("bytes")

    if cached_hash:
        data["sha256"] = cached_hash
    if cached_bytes is not None:
        return data

    try:
        stat = path.stat()
    except OSError:
        data.setdefault("bytes", None)
        return data

    data["bytes"] = stat.st_size
    return data


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _export_metadata_header() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "exported_at": utc_now(),
        "app": {
            "name": "gpt-image-linux",
            "version": config.read_app_version(),
        },
    }


def unique_export_name(path: Path, used_names: set[str]) -> str:
    name = path.name
    base = path.stem
    ext = path.suffix
    counter = 1
    while name in used_names:
        name = f"{base}_{counter}{ext}"
        counter += 1
    used_names.add(name)
    return name


def iter_gallery_zip_chunks(
    entries: Iterable[GalleryEntry | dict[str, Any]],
    skipped: Iterable[dict[str, Any]] | None = None,
    *,
    requested_count: int = 0,
    progress: GalleryZipProgressCallback | None = None,
) -> Generator[bytes, None, GalleryZipFileResult]:
    chunks, result = prepare_gallery_zip_chunks(
        entries,
        skipped=skipped,
        requested_count=requested_count,
        progress=progress,
    )
    yield from chunks
    return result


def prepare_gallery_zip_chunks(
    entries: Iterable[GalleryEntry | dict[str, Any]],
    skipped: Iterable[dict[str, Any]] | None = None,
    *,
    requested_count: int = 0,
    progress: GalleryZipProgressCallback | None = None,
) -> tuple[Iterator[bytes], GalleryZipFileResult]:
    prepared = _prepare_gallery_zip_stream(
        entries,
        skipped=skipped,
        requested_count=requested_count,
        progress=progress,
    )
    result = GalleryZipFileResult(
        requested_count=prepared.requested_count,
        exported_count=prepared.exported_count,
        missing_count=prepared.missing_count,
        bytes_total=prepared.bytes_total,
    )

    def chunks():
        last_emit_at = 0.0
        bytes_written = 0
        _emit_zip_progress(
            progress,
            status="running",
            stage="streaming",
            message="Streaming ZIP archive",
            progress=20,
            bytes_total=prepared.bytes_total,
            bytes_written=0,
            exported_count=prepared.exported_count,
            missing_count=prepared.missing_count,
        )

        try:
            for chunk in prepared.stream:
                if not chunk:
                    continue
                bytes_written += len(chunk)
                now = time.monotonic()
                if now - last_emit_at >= 0.1 or bytes_written >= prepared.bytes_total:
                    last_emit_at = now
                    _emit_zip_progress(
                        progress,
                        status="running",
                        stage="streaming",
                        message="Streaming ZIP archive",
                        progress=20 + round((bytes_written / max(prepared.bytes_total, 1)) * 80),
                        bytes_total=prepared.bytes_total,
                        bytes_written=bytes_written,
                        exported_count=prepared.exported_count,
                        missing_count=prepared.missing_count,
                    )
                yield chunk
        finally:
            for path in prepared.cleanup_paths:
                path.unlink(missing_ok=True)

    return chunks(), result


def _emit_zip_progress(
    callback: GalleryZipProgressCallback | None,
    **updates: Any,
) -> None:
    if not callback:
        return
    callback({key: value for key, value in updates.items() if value is not None})


def _build_export_metadata_from_rows(
    images: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> dict[str, Any]:
    metadata: dict[str, Any] = {**_export_metadata_header(), "images": images}
    if skipped:
        metadata["skipped"] = skipped
    return metadata


def _prepare_gallery_zip_stream(
    entries: Iterable[GalleryEntry | dict[str, Any]],
    skipped: Iterable[dict[str, Any]] | None = None,
    *,
    requested_count: int = 0,
    progress: GalleryZipProgressCallback | None = None,
) -> _PreparedGalleryZip:
    used_names: set[str] = set()
    processed_count = 0
    exported_count = 0
    missing_count = 0
    cleanup_paths: list[Path] = []
    zs = ZipStream(compress_type=zipfile.ZIP_STORED, sized=True)

    skipped_writer = _JsonArrayTempWriter()
    cleanup_paths.append(skipped_writer.path)

    metadata_json_fd, metadata_json_name = tempfile.mkstemp(
        prefix="gallery-metadata-",
        suffix=".json",
    )
    metadata_json_path = Path(metadata_json_name)
    cleanup_paths.append(metadata_json_path)

    metadata_ndjson_fd, metadata_ndjson_name = tempfile.mkstemp(
        prefix="gallery-metadata-",
        suffix=".ndjson",
    )
    metadata_ndjson_path = Path(metadata_ndjson_name)
    cleanup_paths.append(metadata_ndjson_path)

    _emit_zip_progress(
        progress,
        status="running",
        stage="preparing",
        message="Preparing gallery ZIP entries",
        progress=0,
        processed_count=0,
        requested_count=requested_count,
    )

    try:
        with (
            os.fdopen(metadata_json_fd, "w", encoding="utf-8") as metadata_json,
            os.fdopen(metadata_ndjson_fd, "w", encoding="utf-8") as metadata_ndjson,
        ):
            header = _export_metadata_header()
            metadata_json.write("{")
            metadata_json.write(f'"schema_version":{_compact_json(header["schema_version"])}')
            metadata_json.write(f',"exported_at":{_compact_json(header["exported_at"])}')
            metadata_json.write(f',"app":{_compact_json(header["app"])}')
            metadata_json.write(',"images":[')
            metadata_ndjson.write(_compact_json({"type": "header", **header}) + "\n")

            first_image = True
            for raw_skipped in skipped or ():
                skipped_entry = dict(raw_skipped)
                skipped_writer.append(skipped_entry)
                metadata_ndjson.write(
                    _compact_json({"type": "skipped", "entry": skipped_entry}) + "\n"
                )
                missing_count += 1

            initial_skipped_count = missing_count

            for entry in entries:
                processed_count += 1
                path = safe_image_path(_entry_filename(entry))
                if not path or not path.exists():
                    skipped_entry = {
                        "id": _entry_to_dict(entry).get("id"),
                        "filename": _entry_filename(entry),
                        "reason": "image_file_missing",
                    }
                    skipped_writer.append(skipped_entry)
                    metadata_ndjson.write(
                        _compact_json({"type": "skipped", "entry": skipped_entry}) + "\n"
                    )
                    missing_count += 1
                else:
                    name = unique_export_name(path, used_names)
                    metadata_entry = _resolve_export_metadata_for_entry(entry, path)
                    metadata_entry["filename"] = name
                    if not first_image:
                        metadata_json.write(",")
                    first_image = False
                    metadata_json.write(_compact_json(metadata_entry))
                    metadata_ndjson.write(
                        _compact_json({"type": "image", "image": metadata_entry}) + "\n"
                    )
                    exported_count += 1
                    zs.add_path(path, arcname=f"images/{name}")

                denominator = max(requested_count, processed_count + initial_skipped_count, 1)
                prepared_units = min(denominator, processed_count + initial_skipped_count)
                if processed_count == 1 or processed_count % 10 == 0 or prepared_units >= denominator:
                    _emit_zip_progress(
                        progress,
                        status="running",
                        stage="preparing",
                        message="Preparing gallery ZIP entries",
                        progress=min(20, round((prepared_units / denominator) * 20)),
                        processed_count=prepared_units,
                        requested_count=denominator,
                        exported_count=exported_count,
                        missing_count=missing_count,
                    )

            metadata_json.write("]")
            skipped_writer.close()
            if missing_count:
                metadata_json.write(',"skipped":[')
                with open(skipped_writer.path, "r", encoding="utf-8") as skipped_file:
                    shutil.copyfileobj(skipped_file, metadata_json)
                metadata_json.write("]")
            metadata_json.write("}")

        zs.add_path(metadata_json_path, arcname="metadata.json")
        zs.add_path(metadata_ndjson_path, arcname="metadata.ndjson")
        bytes_total = len(zs)
        return _PreparedGalleryZip(
            stream=zs,
            requested_count=max(requested_count, processed_count + initial_skipped_count),
            exported_count=exported_count,
            missing_count=missing_count,
            bytes_total=bytes_total,
            cleanup_paths=cleanup_paths,
        )
    except BaseException:
        try:
            skipped_writer.close()
        except Exception:
            pass
        for path in cleanup_paths:
            path.unlink(missing_ok=True)
        raise


def write_gallery_zip_file(
    entries: Iterable[GalleryEntry | dict[str, Any]],
    destination: Path,
    *,
    requested_count: int = 0,
    skipped: Iterable[dict[str, Any]] | None = None,
    progress: GalleryZipProgressCallback | None = None,
) -> GalleryZipFileResult:
    """Write a ZIP archive to disk while reporting deterministic pack progress."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(f"{destination.name}.tmp")
    temp_path.unlink(missing_ok=True)

    prepared = _prepare_gallery_zip_stream(
        entries,
        skipped=skipped,
        requested_count=requested_count,
        progress=progress,
    )
    bytes_written = 0
    last_emit_at = 0.0

    _emit_zip_progress(
        progress,
        status="running",
        stage="packing",
        message="Writing ZIP archive",
        progress=20,
        bytes_total=prepared.bytes_total,
        bytes_written=0,
        exported_count=prepared.exported_count,
        missing_count=prepared.missing_count,
    )

    try:
        with open(temp_path, "wb") as f:
            for chunk in prepared.stream:
                if not chunk:
                    continue
                f.write(chunk)
                bytes_written += len(chunk)
                now = time.monotonic()
                if now - last_emit_at >= 0.1 or bytes_written >= prepared.bytes_total:
                    last_emit_at = now
                    _emit_zip_progress(
                        progress,
                        status="running",
                        stage="packing",
                        message="Writing ZIP archive",
                        progress=20 + round((bytes_written / max(prepared.bytes_total, 1)) * 80),
                        bytes_total=prepared.bytes_total,
                        bytes_written=bytes_written,
                        exported_count=prepared.exported_count,
                        missing_count=prepared.missing_count,
                    )
        temp_path.replace(destination)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise
    finally:
        for path in prepared.cleanup_paths:
            path.unlink(missing_ok=True)

    return GalleryZipFileResult(
        requested_count=prepared.requested_count,
        exported_count=prepared.exported_count,
        missing_count=prepared.missing_count,
        bytes_total=prepared.bytes_total,
    )



