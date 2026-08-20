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
from .blocking import run_file_operation

from ..core import settings as config
from ..core.utils import utc_now
from ..repositories.image_files import safe_image_path, validate_image_bytes
from ..schemas.gallery import GalleryEntry
from ..api.uploads import IMAGE_UPLOAD_CONTENT_TYPES, IMAGE_UPLOAD_EXTENSIONS

GalleryZipProgressCallback = Callable[[dict[str, Any]], None]
from .gallery_archive_shared import *

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
    max_stem_bytes = max(
        1,
        MAX_IMPORT_FILENAME_BYTES
        - len(suffix.encode("utf-8"))
        - IMPORT_FILENAME_DEDUPE_SUFFIX_BYTES,
    )
    safe_stem = _truncate_filename_stem(safe_stem or uuid.uuid4().hex, max_stem_bytes)
    return f"{safe_stem or uuid.uuid4().hex}{suffix}"


def _truncate_filename_stem(stem: str, max_bytes: int) -> str:
    candidate = str(stem or "")
    encoded = candidate.encode("utf-8")
    if len(encoded) > max_bytes:
        candidate = encoded[:max_bytes].decode("utf-8", "ignore")
    return candidate.strip("._")


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


def validate_import_zip_infos(zf: zipfile.ZipFile) -> ImportZipManifest:
    file_infos = [info for info in zf.infolist() if not info.is_dir()]
    if len(file_infos) > config.IMPORT_MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail="Import archive contains too many files",
        )

    names: set[str] = set()
    total_uncompressed = 0
    total_compressed = 0
    metadata_info: zipfile.ZipInfo | None = None
    metadata_ndjson_info: zipfile.ZipInfo | None = None
    for info in file_infos:
        if not is_safe_zip_member_name(info.filename):
            raise HTTPException(status_code=400, detail="Import archive contains unsafe paths")
        if info.filename in names:
            raise HTTPException(status_code=400, detail="Import archive contains duplicate paths")
        if info.filename == "metadata.json":
            metadata_info = info
        elif info.filename == "metadata.ndjson":
            metadata_ndjson_info = info
        elif Path(info.filename).suffix.lower() in IMAGE_UPLOAD_EXTENSIONS:
            if info.file_size > max_upload_bytes():
                raise HTTPException(status_code=400, detail="Imported image is too large")

        total_uncompressed += info.file_size
        total_compressed += info.compress_size
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

    if total_compressed > 0 and total_uncompressed / total_compressed > config.IMPORT_MAX_COMPRESSION_RATIO:
        raise HTTPException(
            status_code=400,
            detail="Import archive aggregate compression ratio exceeds limit",
        )

    if metadata_info is None and metadata_ndjson_info is None:
        raise HTTPException(status_code=400, detail="metadata.json is required")
    selected_metadata = metadata_ndjson_info or metadata_info
    if selected_metadata and selected_metadata.file_size > config.IMPORT_MAX_METADATA_BYTES:
        metadata_name = "metadata.ndjson" if metadata_ndjson_info else "metadata.json"
        raise HTTPException(status_code=400, detail=f"{metadata_name} is too large")

    return ImportZipManifest(
        names=names,
        use_ndjson=metadata_ndjson_info is not None,
    )


def iter_import_gallery_entries(
    zip_path: Path,
    *,
    progress: GalleryZipProgressCallback | None = None,
) -> Iterator[tuple[bytes, dict]]:
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail="Import file must be a valid ZIP") from e

    with zf:
        yield from _iter_zip_import_entries(zf, progress=progress)


def count_import_gallery_entries(zip_path: Path) -> int:
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail="Import file must be a valid ZIP") from e

    with zf:
        manifest = validate_import_zip_infos(zf)
        if manifest.use_ndjson:
            raw_images: Iterable[dict[str, Any]] = _iter_import_metadata_ndjson(zf)
        else:
            metadata = _read_import_metadata_json(zf)
            raw_images_value = metadata.get("images")
            if not isinstance(raw_images_value, list):
                raise HTTPException(status_code=400, detail="metadata.json images must be a list")
            if len(raw_images_value) > config.IMPORT_MAX_ENTRIES:
                raise HTTPException(status_code=400, detail="Import metadata contains too many entries")
            raw_images = raw_images_value

        count = 0
        seen_members: set[str] = set()
        for raw_entry in raw_images:
            if not isinstance(raw_entry, dict):
                continue
            if not _metadata_entry_has_importable_image(raw_entry, manifest.names):
                continue
            zip_name = _metadata_entry_zip_name(raw_entry, manifest.names)
            if zip_name in seen_members:
                raise HTTPException(
                    status_code=400,
                    detail="Import metadata references an image more than once",
                )
            seen_members.add(zip_name)
            count += 1
            if count > config.IMPORT_MAX_ENTRIES:
                raise HTTPException(status_code=400, detail="Import metadata contains too many entries")
        return count


async def stream_upload_to_tempfile(
    archive: UploadFile,
    max_bytes: int,
    *,
    directory: Path | None = None,
) -> Path:
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
    def copy_upload() -> tuple[Path, int]:
        fd, tmp_name = tempfile.mkstemp(
            prefix="gallery-import-",
            suffix=".zip",
            dir=str(directory) if directory is not None else None,
        )
        tmp_path = Path(tmp_name)
        total = 0
        chunk_size = 1024 * 1024
        try:
            archive.file.seek(0)
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = archive.file.read(chunk_size)
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
        return tmp_path, total

    tmp_path, total = await run_file_operation(
        copy_upload,
        metric_name="copy_gallery_import_upload",
    )

    if total == 0:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Uploaded archive is empty")

    return tmp_path


def _read_import_metadata_json(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        with zf.open("metadata.json") as stream:
            raw_metadata = stream.read(config.IMPORT_MAX_METADATA_BYTES + 1)
    except KeyError as e:
        raise HTTPException(status_code=400, detail="metadata.json is required") from e

    if len(raw_metadata) > config.IMPORT_MAX_METADATA_BYTES:
        raise HTTPException(status_code=400, detail="metadata.json is too large")

    try:
        metadata = json.loads(raw_metadata.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=400, detail="metadata.json is invalid") from e
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="metadata.json is invalid")
    return metadata


def _iter_import_metadata_ndjson(zf: zipfile.ZipFile) -> Iterator[dict[str, Any]]:
    try:
        stream = zf.open("metadata.ndjson")
    except KeyError as e:
        raise HTTPException(status_code=400, detail="metadata.ndjson is required") from e

    with stream:
        total_bytes = 0
        image_records = 0
        while True:
            raw_line = stream.readline(config.IMPORT_MAX_METADATA_BYTES + 1)
            if not raw_line:
                break
            total_bytes += len(raw_line)
            if len(raw_line) > config.IMPORT_MAX_METADATA_BYTES:
                raise HTTPException(status_code=400, detail="metadata.ndjson line is too large")
            if total_bytes > config.IMPORT_MAX_METADATA_BYTES:
                raise HTTPException(status_code=400, detail="metadata.ndjson is too large")
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise HTTPException(status_code=400, detail="metadata.ndjson is invalid") from e
            if not isinstance(record, dict):
                continue
            if str(record.get("type") or "") != "image":
                continue
            image = record.get("image")
            if isinstance(image, dict):
                image_records += 1
                if image_records > config.IMPORT_MAX_ENTRIES:
                    raise HTTPException(
                        status_code=400,
                        detail="Import metadata contains too many entries",
                    )
                yield image


def _metadata_entry_has_importable_image(raw_entry: dict[str, Any], names: set[str]) -> bool:
    zip_name = _metadata_entry_zip_name(raw_entry, names)
    return zip_name in names and Path(zip_name).suffix.lower() in IMAGE_UPLOAD_EXTENSIONS


def _metadata_entry_zip_name(raw_entry: dict[str, Any], names: set[str]) -> str:
    exported_filename = str(raw_entry.get("filename") or "")
    return exported_filename if exported_filename in names else f"images/{exported_filename}"


def _emit_import_progress(
    callback: GalleryZipProgressCallback | None,
    processed_count: int,
    importable_count: int,
    skipped_count: int,
) -> None:
    _emit_zip_progress(
        callback,
        status="running",
        stage="validating",
        message="Validating import archive entries",
        processed_count=processed_count,
        exported_count=importable_count,
        missing_count=skipped_count,
    )


def _iter_zip_import_entries(
    zf: zipfile.ZipFile,
    *,
    progress: GalleryZipProgressCallback | None = None,
) -> Iterator[tuple[bytes, dict]]:
    manifest = validate_import_zip_infos(zf)
    names = manifest.names
    if manifest.use_ndjson:
        raw_images: Iterable[dict[str, Any]] = _iter_import_metadata_ndjson(zf)
    else:
        metadata = _read_import_metadata_json(zf)
        raw_metadata_images = metadata.get("images")
        if not isinstance(raw_metadata_images, list):
            raise HTTPException(status_code=400, detail="metadata.json images must be a list")
        if len(raw_metadata_images) > config.IMPORT_MAX_ENTRIES:
            raise HTTPException(status_code=400, detail="Import metadata contains too many entries")
        raw_images = raw_metadata_images

    used_names: set[str] = set()
    used_ids: set[str] = set()
    processed_count = 0
    importable_count = 0
    skipped_count = 0
    seen_members: set[str] = set()
    output_bytes = 0
    next_filename_suffix: dict[tuple[str, str], int] = {}

    for raw_entry in raw_images:
        if not isinstance(raw_entry, dict):
            continue
        processed_count += 1
        if processed_count > config.IMPORT_MAX_ENTRIES:
            raise HTTPException(status_code=400, detail="Import metadata contains too many entries")

        exported_filename = str(raw_entry.get("filename") or "")
        zip_name = _metadata_entry_zip_name(raw_entry, names)
        if zip_name not in names:
            skipped_count += 1
            _emit_import_progress(progress, processed_count, importable_count, skipped_count)
            continue
        if Path(zip_name).suffix.lower() not in IMAGE_UPLOAD_EXTENSIONS:
            skipped_count += 1
            _emit_import_progress(progress, processed_count, importable_count, skipped_count)
            continue
        if zip_name in seen_members:
            raise HTTPException(
                status_code=400,
                detail="Import metadata references an image more than once",
            )
        seen_members.add(zip_name)

        try:
            with zf.open(zip_name) as f:
                limit = max_upload_bytes()
                image_bytes = f.read(limit + 1)
        except KeyError:
            skipped_count += 1
            _emit_import_progress(progress, processed_count, importable_count, skipped_count)
            continue
        if not image_bytes:
            skipped_count += 1
            _emit_import_progress(progress, processed_count, importable_count, skipped_count)
            continue
        if len(image_bytes) > limit:
            raise HTTPException(
                status_code=400,
                detail="Imported image is too large",
            )
        output_bytes += len(image_bytes)
        if output_bytes > import_max_output_bytes():
            raise HTTPException(
                status_code=400,
                detail="Imported image output size exceeds limit",
            )
        try:
            validate_image_bytes(
                image_bytes,
                filename=Path(exported_filename or zip_name).name,
                content_type=IMAGE_UPLOAD_CONTENT_TYPES.get(
                    Path(zip_name).suffix.lower(),
                    "",
                ),
            )
        except ValueError:
            skipped_count += 1
            _emit_import_progress(progress, processed_count, importable_count, skipped_count)
            continue

        original_name = Path(exported_filename or zip_name).name
        filename = sanitize_import_filename(original_name)
        base = Path(filename).stem
        ext = Path(filename).suffix
        while filename in used_names:
            key = (base, ext)
            counter = next_filename_suffix.get(key, 1)
            filename = f"{base}_{counter}{ext}"
            next_filename_suffix[key] = counter + 1
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
        importable_count += 1
        _emit_import_progress(progress, processed_count, importable_count, skipped_count)
        yield image_bytes, entry
