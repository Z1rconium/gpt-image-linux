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


@dataclass(frozen=True)
class ImportZipManifest:
    names: set[str]
    use_ndjson: bool


@dataclass
class _PreparedGalleryZip:
    stream: ZipStream
    requested_count: int
    exported_count: int
    missing_count: int
    bytes_total: int
    cleanup_paths: list[Path]


class _JsonArrayTempWriter:
    def __init__(self, *, suffix: str = ".json") -> None:
        fd, tmp_name = tempfile.mkstemp(prefix="gallery-metadata-", suffix=suffix)
        self.path = Path(tmp_name)
        self._file = os.fdopen(fd, "w", encoding="utf-8")
        self._first = True
        self.count = 0

    def append(self, value: dict[str, Any]) -> None:
        if not self._first:
            self._file.write(",")
        self._first = False
        self._file.write(_compact_json(value))
        self.count += 1

    def close(self) -> None:
        self._file.close()

    def cleanup(self) -> None:
        self.path.unlink(missing_ok=True)


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
                path = storage.safe_image_path(_entry_filename(entry))
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


def validate_import_zip_infos(zf: zipfile.ZipFile) -> ImportZipManifest:
    file_infos = [info for info in zf.infolist() if not info.is_dir()]
    if len(file_infos) > config.IMPORT_MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail="Import archive contains too many files",
        )

    names: set[str] = set()
    total_uncompressed = 0
    metadata_info: zipfile.ZipInfo | None = None
    metadata_ndjson_info: zipfile.ZipInfo | None = None
    for info in file_infos:
        if not is_safe_zip_member_name(info.filename):
            raise HTTPException(status_code=400, detail="Import archive contains unsafe paths")
        if info.filename == "metadata.json":
            metadata_info = info
        elif info.filename == "metadata.ndjson":
            metadata_ndjson_info = info
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

    if metadata_info is None and metadata_ndjson_info is None:
        raise HTTPException(status_code=400, detail="metadata.json is required")
    if (
        metadata_info is not None
        and metadata_ndjson_info is None
        and metadata_info.file_size > config.IMPORT_MAX_METADATA_BYTES
    ):
        raise HTTPException(status_code=400, detail="metadata.json is too large")

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
            return sum(
                1
                for entry in _iter_import_metadata_ndjson(zf)
                if _metadata_entry_has_importable_image(entry, manifest.names)
            )
        metadata = _read_import_metadata_json(zf)
        raw_images = metadata.get("images")
        if not isinstance(raw_images, list):
            raise HTTPException(status_code=400, detail="metadata.json images must be a list")
        return sum(
            1
            for entry in raw_images
            if isinstance(entry, dict)
            and _metadata_entry_has_importable_image(entry, manifest.names)
        )


async def stream_upload_to_tempfile(
    archive: UploadFile,
    max_bytes: int,
    *,
    directory: Path | None = None,
) -> Path:
    if directory is not None:
        directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix="gallery-import-",
        suffix=".zip",
        dir=str(directory) if directory is not None else None,
    )
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


def _read_import_metadata_json(zf: zipfile.ZipFile) -> dict[str, Any]:
    try:
        raw_metadata = zf.read("metadata.json")
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
        for raw_line in stream:
            if len(raw_line) > config.IMPORT_MAX_METADATA_BYTES:
                raise HTTPException(status_code=400, detail="metadata.ndjson line is too large")
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
                yield image


def _metadata_entry_has_importable_image(raw_entry: dict[str, Any], names: set[str]) -> bool:
    exported_filename = str(raw_entry.get("filename") or "")
    zip_name = exported_filename if exported_filename in names else f"images/{exported_filename}"
    return zip_name in names and Path(zip_name).suffix.lower() in IMAGE_UPLOAD_EXTENSIONS


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
        raw_images = raw_metadata_images

    used_names: set[str] = set()
    used_ids: set[str] = set()
    processed_count = 0
    importable_count = 0
    skipped_count = 0

    for raw_entry in raw_images:
        if not isinstance(raw_entry, dict):
            continue
        processed_count += 1

        exported_filename = str(raw_entry.get("filename") or "")
        zip_name = exported_filename if exported_filename in names else f"images/{exported_filename}"
        if zip_name not in names:
            skipped_count += 1
            _emit_import_progress(progress, processed_count, importable_count, skipped_count)
            continue
        if Path(zip_name).suffix.lower() not in IMAGE_UPLOAD_EXTENSIONS:
            skipped_count += 1
            _emit_import_progress(progress, processed_count, importable_count, skipped_count)
            continue

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
            skipped_count += 1
            _emit_import_progress(progress, processed_count, importable_count, skipped_count)
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
        importable_count += 1
        _emit_import_progress(progress, processed_count, importable_count, skipped_count)
        yield image_bytes, entry
