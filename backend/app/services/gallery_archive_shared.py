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

MAX_IMPORT_FILENAME_BYTES = 240
IMPORT_FILENAME_DEDUPE_SUFFIX_BYTES = 16


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


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _emit_zip_progress(
    callback: GalleryZipProgressCallback | None,
    **updates: Any,
) -> None:
    if callback:
        callback({key: value for key, value in updates.items() if value is not None})


def max_upload_bytes() -> int:
    return config.MAX_FILE_SIZE_MB * 1024 * 1024


def import_archive_max_bytes() -> int:
    return config.IMPORT_ARCHIVE_MAX_MB * 1024 * 1024


def import_max_uncompressed_bytes() -> int:
    return config.IMPORT_MAX_UNCOMPRESSED_MB * 1024 * 1024


def import_max_output_bytes() -> int:
    return config.IMPORT_MAX_OUTPUT_MB * 1024 * 1024


_GALLERY_ENTRY_EXPORT_FIELDS = tuple(
    name for name in GalleryEntry.model_fields
    if name not in {"image_url", "thumbnail_filename", "thumbnail_url"}
)



__all__ = [name for name in globals() if not name.startswith("__")]
