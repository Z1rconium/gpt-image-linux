import json
import os
import re
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
from ..repositories import storage
from ..schemas.models import GalleryEntry
from .uploads import IMAGE_UPLOAD_CONTENT_TYPES, IMAGE_UPLOAD_EXTENSIONS

GalleryZipProgressCallback = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class GalleryZipFileResult:
    requested_count: int
    exported_count: int
    missing_count: int
    bytes_total: int


def max_upload_bytes() -> int:
    return config.MAX_FILE_SIZE_MB * 1024 * 1024


def import_archive_max_bytes() -> int:
    return config.IMPORT_ARCHIVE_MAX_MB * 1024 * 1024


def import_max_uncompressed_bytes() -> int:
    return config.IMPORT_MAX_UNCOMPRESSED_MB * 1024 * 1024


_GALLERY_ENTRY_EXPORT_FIELDS = tuple(
    name for name in GalleryEntry.model_fields
    if name not in {"image_url", "thumbnail_filename", "thumbnail_url"}
)


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
    used_names: set[str] = set()
    metadata_images: list[dict[str, Any]] = []
    skipped_entries: list[dict[str, Any]] = list(skipped or [])
    initial_skipped_count = len(skipped_entries)
    processed_count = 0
    zs = ZipStream(compress_type=zipfile.ZIP_STORED, sized=True)
    last_emit_at = 0.0

    _emit_zip_progress(
        progress,
        status="running",
        stage="preparing",
        message="Preparing gallery ZIP entries",
        progress=0,
        processed_count=0,
        requested_count=requested_count,
    )

    for entry in entries:
        processed_count += 1
        path = storage.safe_image_path(_entry_filename(entry))
        if not path or not path.exists():
            skipped_entries.append(
                {
                    "id": _entry_to_dict(entry).get("id"),
                    "filename": _entry_filename(entry),
                    "reason": "image_file_missing",
                }
            )
        else:
            name = unique_export_name(path, used_names)
            metadata_entry = _resolve_export_metadata_for_entry(entry, path)
            metadata_entry["filename"] = name
            metadata_images.append(metadata_entry)
            zs.add_path(path, arcname=f"images/{name}")

        denominator = max(requested_count, processed_count, 1)
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
                exported_count=len(metadata_images),
                missing_count=len(skipped_entries),
            )

    metadata = _build_export_metadata_from_rows(metadata_images, skipped_entries)
    zs.add(
        json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8"),
        arcname="metadata.json",
    )

    bytes_total = len(zs)
    bytes_written = 0
    _emit_zip_progress(
        progress,
        status="running",
        stage="streaming",
        message="Streaming ZIP archive",
        progress=20,
        bytes_total=bytes_total,
        bytes_written=0,
        exported_count=len(metadata_images),
        missing_count=len(skipped_entries),
    )

    for chunk in zs:
        if not chunk:
            continue
        bytes_written += len(chunk)
        now = time.monotonic()
        if now - last_emit_at >= 0.1 or bytes_written >= bytes_total:
            last_emit_at = now
            _emit_zip_progress(
                progress,
                status="running",
                stage="streaming",
                message="Streaming ZIP archive",
                progress=20 + round((bytes_written / max(bytes_total, 1)) * 80),
                bytes_total=bytes_total,
                bytes_written=bytes_written,
                exported_count=len(metadata_images),
                missing_count=len(skipped_entries),
            )
        yield chunk

    return GalleryZipFileResult(
        requested_count=max(requested_count, processed_count + initial_skipped_count),
        exported_count=len(metadata_images),
        missing_count=len(skipped_entries),
        bytes_total=bytes_total,
    )


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
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "exported_at": utc_now(),
        "app": {
            "name": "gpt-image-linux",
            "version": config.read_app_version(),
        },
        "images": images,
    }
    if skipped:
        metadata["skipped"] = skipped
    return metadata


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

    used_names: set[str] = set()
    metadata_images: list[dict[str, Any]] = []
    skipped_entries: list[dict[str, Any]] = list(skipped or [])
    initial_skipped_count = len(skipped_entries)
    processed_count = 0
    zs = ZipStream(compress_type=zipfile.ZIP_STORED, sized=True)

    _emit_zip_progress(
        progress,
        status="running",
        stage="preparing",
        message="Preparing gallery ZIP entries",
        progress=0,
        processed_count=0,
        requested_count=requested_count,
    )

    for entry in entries:
        processed_count += 1
        path = storage.safe_image_path(_entry_filename(entry))
        if not path or not path.exists():
            skipped_entries.append(
                {
                    "id": _entry_to_dict(entry).get("id"),
                    "filename": _entry_filename(entry),
                    "reason": "image_file_missing",
                }
            )
        else:
            name = unique_export_name(path, used_names)
            metadata_entry = _resolve_export_metadata_for_entry(entry, path)
            metadata_entry["filename"] = name
            metadata_images.append(metadata_entry)
            zs.add_path(path, arcname=f"images/{name}")

        denominator = max(requested_count, processed_count, 1)
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
                exported_count=len(metadata_images),
                missing_count=len(skipped_entries),
            )

    metadata = _build_export_metadata_from_rows(metadata_images, skipped_entries)
    metadata_bytes = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    zs.add(metadata_bytes, arcname="metadata.json")
    bytes_total = len(zs)
    bytes_written = 0
    last_emit_at = 0.0

    _emit_zip_progress(
        progress,
        status="running",
        stage="packing",
        message="Writing ZIP archive",
        progress=20,
        bytes_total=bytes_total,
        bytes_written=0,
        exported_count=len(metadata_images),
        missing_count=len(skipped_entries),
    )

    try:
        with open(temp_path, "wb") as f:
            for chunk in zs:
                if not chunk:
                    continue
                f.write(chunk)
                bytes_written += len(chunk)
                now = time.monotonic()
                if now - last_emit_at >= 0.1 or bytes_written >= bytes_total:
                    last_emit_at = now
                    _emit_zip_progress(
                        progress,
                        status="running",
                        stage="packing",
                        message="Writing ZIP archive",
                        progress=20 + round((bytes_written / max(bytes_total, 1)) * 80),
                        bytes_total=bytes_total,
                        bytes_written=bytes_written,
                        exported_count=len(metadata_images),
                        missing_count=len(skipped_entries),
                    )
        temp_path.replace(destination)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise

    return GalleryZipFileResult(
        requested_count=requested_count,
        exported_count=len(metadata_images),
        missing_count=len(skipped_entries),
        bytes_total=bytes_total,
    )


def sanitize_import_filename(filename: str, fallback_ext: str = ".png") -> str:
    name = Path(filename or "").name
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_UPLOAD_EXTENSIONS:
        suffix = fallback_ext if fallback_ext in IMAGE_UPLOAD_EXTENSIONS else ".png"
    stem = Path(name).stem or uuid.uuid4().hex
    safe_stem = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in stem
    ).strip("._")
    return f"{safe_stem or uuid.uuid4().hex}{suffix}"


def is_safe_zip_member_name(filename: str) -> bool:
    if "\\" in filename:
        return False
    path = PurePosixPath(filename)
    return bool(
        filename
        and not path.is_absolute()
        and not re.match(r"^[A-Za-z]:/", filename)
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def validate_import_zip_infos(zf: zipfile.ZipFile) -> set[str]:
    file_infos = [info for info in zf.infolist() if not info.is_dir()]
    if len(file_infos) > config.IMPORT_MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail="Import archive contains too many files",
        )

    names: set[str] = set()
    total_uncompressed = 0
    metadata_info: zipfile.ZipInfo | None = None
    for info in file_infos:
        if not is_safe_zip_member_name(info.filename):
            raise HTTPException(status_code=400, detail="Import archive contains unsafe paths")
        if info.filename == "metadata.json":
            metadata_info = info
        elif Path(info.filename).suffix.lower() in IMAGE_UPLOAD_EXTENSIONS:
            if info.file_size > max_upload_bytes():
                raise HTTPException(status_code=400, detail="Imported image is too large")

        total_uncompressed += info.file_size
        if total_uncompressed > import_max_uncompressed_bytes():
            raise HTTPException(
                status_code=400,
                detail="Import archive uncompressed size exceeds limit",
            )
        if (
            info.file_size > 0
            and (
                info.compress_size == 0
                or info.file_size / info.compress_size > config.IMPORT_MAX_COMPRESSION_RATIO
            )
        ):
            raise HTTPException(
                status_code=400,
                detail="Import archive compression ratio exceeds limit",
            )
        names.add(info.filename)

    if metadata_info is None:
        raise HTTPException(status_code=400, detail="metadata.json is required")
    if metadata_info.file_size > config.IMPORT_MAX_METADATA_BYTES:
        raise HTTPException(status_code=400, detail="metadata.json is too large")

    return names


def iter_import_gallery_entries(zip_path: Path) -> Iterator[tuple[bytes, dict]]:
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail="Import file must be a valid ZIP") from e

    with zf:
        yield from _iter_zip_import_entries(zf)


async def stream_upload_to_tempfile(archive: UploadFile, max_bytes: int) -> Path:
    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    tmp_path = Path(tmp_name)
    total = 0
    chunk_size = 1024 * 1024
    try:
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = await archive.read(chunk_size)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=400,
                        detail="Uploaded archive is too large",
                    )
                out.write(chunk)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    if total == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded archive is empty")

    return tmp_path


def _iter_zip_import_entries(zf: zipfile.ZipFile) -> Iterator[tuple[bytes, dict]]:
    names = validate_import_zip_infos(zf)
    try:
        metadata = json.loads(zf.read("metadata.json").decode("utf-8"))
    except KeyError as e:
        raise HTTPException(status_code=400, detail="metadata.json is required") from e
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail="metadata.json is invalid") from e

    raw_images = metadata.get("images")
    if not isinstance(raw_images, list):
        raise HTTPException(status_code=400, detail="metadata.json images must be a list")

    used_names: set[str] = set()
    used_ids: set[str] = set()

    for raw_entry in raw_images:
        if not isinstance(raw_entry, dict):
            continue

        exported_filename = str(raw_entry.get("filename") or "")
        zip_name = exported_filename if exported_filename in names else f"images/{exported_filename}"
        if zip_name not in names:
            continue
        if Path(zip_name).suffix.lower() not in IMAGE_UPLOAD_EXTENSIONS:
            continue

        try:
            with zf.open(zip_name) as f:
                limit = max_upload_bytes()
                image_bytes = f.read(limit + 1)
        except KeyError:
            continue
        if not image_bytes:
            continue
        if len(image_bytes) > limit:
            raise HTTPException(
                status_code=400,
                detail="Imported image is too large",
            )
        try:
            storage.validate_image_bytes(
                image_bytes,
                filename=Path(exported_filename or zip_name).name,
                content_type=IMAGE_UPLOAD_CONTENT_TYPES.get(
                    Path(zip_name).suffix.lower(),
                    "",
                ),
            )
        except ValueError:
            continue

        original_name = Path(exported_filename or zip_name).name
        filename = sanitize_import_filename(original_name)
        base = Path(filename).stem
        ext = Path(filename).suffix
        counter = 1
        while filename in used_names:
            filename = f"{base}_{counter}{ext}"
            counter += 1
        used_names.add(filename)

        image_id = str(raw_entry.get("id") or uuid.uuid4())
        while image_id in used_ids:
            image_id = str(uuid.uuid4())
        used_ids.add(image_id)

        entry = {
            **raw_entry,
            "id": image_id,
            "filename": filename,
            "created_at": str(raw_entry.get("created_at") or utc_now()),
        }
        yield image_bytes, entry
