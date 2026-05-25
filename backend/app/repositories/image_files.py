import struct
import tempfile
import uuid
from pathlib import Path

from ..core import settings as config

IMAGE_FILE_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".webp",
}

IMAGE_EXTENSION_FORMATS = {
    ".avif": "avif",
    ".bmp": "bmp",
    ".gif": "gif",
    ".heic": "heif",
    ".heif": "heif",
    ".ico": "ico",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".webp": "webp",
}
IMAGE_CONTENT_TYPE_FORMATS = {
    "image/avif": "avif",
    "image/bmp": "bmp",
    "image/gif": "gif",
    "image/heic": "heif",
    "image/heif": "heif",
    "image/ico": "ico",
    "image/icon": "ico",
    "image/jpeg": "jpeg",
    "image/pjpeg": "jpeg",
    "image/png": "png",
    "image/tiff": "tiff",
    "image/vnd.microsoft.icon": "ico",
    "image/webp": "webp",
    "image/x-icon": "ico",
}
IMAGE_FORMAT_CONTENT_TYPES = {
    "avif": "image/avif",
    "bmp": "image/bmp",
    "gif": "image/gif",
    "heif": "image/heif",
    "ico": "image/x-icon",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "tiff": "image/tiff",
    "webp": "image/webp",
}
THUMBNAIL_EXTENSION = ".webp"
THUMBNAIL_CONTENT_TYPE = "image/webp"


def generate_image_id() -> str:
    return str(uuid.uuid4())


def detect_image_format(image_bytes: bytes) -> str | None:
    stripped = image_bytes[:512].lstrip().lower()
    if stripped.startswith((b"<svg", b"<?xml", b"<!doctype html", b"<html")):
        return None
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if image_bytes.startswith(b"\xff\xd8"):
        return "jpeg"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if image_bytes.startswith(b"RIFF") and len(image_bytes) >= 12 and image_bytes[8:12] == b"WEBP":
        return "webp"
    if image_bytes.startswith(b"BM"):
        return "bmp"
    if image_bytes.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if len(image_bytes) >= 12 and image_bytes[4:8] == b"ftyp":
        brand = image_bytes[8:12]
        compatible = image_bytes[8:32]
        if brand in {b"avif", b"avis"} or b"avif" in compatible or b"avis" in compatible:
            return "avif"
        if brand in {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"}:
            return "heif"
    if image_bytes.startswith(b"\x00\x00\x01\x00"):
        return "ico"
    return None


def validate_image_bytes(
    image_bytes: bytes,
    *,
    filename: str = "",
    content_type: str = "",
) -> str:
    detected_format = detect_image_format(image_bytes)
    if not detected_format:
        raise ValueError("Image data must be a supported raster image format")

    suffix = Path(filename or "").suffix.lower()
    extension_format = IMAGE_EXTENSION_FORMATS.get(suffix)
    if suffix and extension_format != detected_format:
        raise ValueError("Image file extension does not match image data")

    normalized_content_type = (content_type or "").split(";", 1)[0].strip().lower()
    content_type_format = IMAGE_CONTENT_TYPE_FORMATS.get(normalized_content_type)
    if normalized_content_type and content_type_format != detected_format:
        raise ValueError("Image content type does not match image data")

    return detected_format


def get_image_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(image_bytes) >= 24:
        return struct.unpack(">II", image_bytes[16:24])

    if image_bytes.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 < len(image_bytes):
            if image_bytes[offset] != 0xFF:
                offset += 1
                continue
            marker = image_bytes[offset + 1]
            offset += 2
            while marker == 0xFF and offset < len(image_bytes):
                marker = image_bytes[offset]
                offset += 1
            if marker in (0xD8, 0xD9):
                continue
            if offset + 2 > len(image_bytes):
                return None
            segment_length = struct.unpack(">H", image_bytes[offset : offset + 2])[0]
            if segment_length < 2 or offset + segment_length > len(image_bytes):
                return None
            if marker in (
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            ):
                height, width = struct.unpack(">HH", image_bytes[offset + 3 : offset + 7])
                return width, height
            offset += segment_length

    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        chunk_type = image_bytes[12:16]
        if chunk_type == b"VP8X" and len(image_bytes) >= 30:
            width = int.from_bytes(image_bytes[24:27], "little") + 1
            height = int.from_bytes(image_bytes[27:30], "little") + 1
            return width, height
        if chunk_type == b"VP8 " and len(image_bytes) >= 30:
            width, height = struct.unpack("<HH", image_bytes[26:30])
            return width & 0x3FFF, height & 0x3FFF
        if chunk_type == b"VP8L" and len(image_bytes) >= 25 and image_bytes[20] == 0x2F:
            bits = int.from_bytes(image_bytes[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return width, height

    return None


def image_dimension_metadata(image_bytes: bytes) -> dict[str, int]:
    dimensions = get_image_dimensions(image_bytes)
    if not dimensions:
        return {}
    width, height = dimensions
    return {"image_width": width, "image_height": height}


def _safe_path(filename: str, base_dir: str, allowed_suffixes: set[str]) -> Path | None:
    if not filename or "\x00" in filename or "/" in filename or "\\" in filename:
        return None
    if filename in {".", ".."}:
        return None

    path_name = Path(filename)
    if path_name.name != filename or path_name.suffix.lower() not in allowed_suffixes:
        return None

    root = Path(base_dir).resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def safe_image_path(filename: str) -> Path | None:
    return _safe_path(filename, config.IMAGES_DIR, IMAGE_FILE_EXTENSIONS)


def safe_thumbnail_path(filename: str) -> Path | None:
    return _safe_path(filename, config.THUMBNAILS_DIR, {THUMBNAIL_EXTENSION})


def save_image_to_temp(image_bytes: bytes, filename: str) -> Path:
    validate_image_bytes(image_bytes, filename=filename)
    path = safe_image_path(filename)
    if not path:
        raise ValueError(f"Invalid image filename: {filename}")

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_file = tempfile.NamedTemporaryFile(
        prefix=f".{path.stem}-",
        suffix=f"{path.suffix}.tmp",
        dir=path.parent,
        delete=False,
    )
    temp_path = Path(temp_file.name)
    try:
        with temp_file:
            temp_file.write(image_bytes)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path


def promote_image_temp(filename: str, temp_path: Path) -> Path:
    path = safe_image_path(filename)
    if not path:
        temp_path.unlink(missing_ok=True)
        raise ValueError(f"Invalid image filename: {filename}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.replace(path)
    return path


def delete_image_from_disk(filename: str) -> bool:
    path = safe_image_path(filename)
    if path and path.is_file():
        path.unlink()
        return True
    return False


def scan_image_files() -> set[str]:
    images_dir = Path(config.IMAGES_DIR)
    if not images_dir.exists():
        return set()
    return {
        path.name
        for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_FILE_EXTENSIONS
    }
