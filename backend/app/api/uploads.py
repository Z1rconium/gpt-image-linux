from pathlib import Path

from fastapi import HTTPException, UploadFile

from ..repositories.image_files import (
    IMAGE_CONTENT_TYPE_FORMATS,
    IMAGE_EXTENSION_FORMATS,
    IMAGE_FILE_EXTENSIONS,
    IMAGE_FORMAT_CONTENT_TYPES,
    image_content_type_for_filename,
    validate_image_header_bytes,
)


IMAGE_UPLOAD_EXTENSIONS = IMAGE_FILE_EXTENSIONS
IMAGE_UPLOAD_CONTENT_TYPES = {
    extension: IMAGE_FORMAT_CONTENT_TYPES[image_format]
    for extension, image_format in IMAGE_EXTENSION_FORMATS.items()
}


def resolve_upload_content_type(upload: UploadFile) -> str:
    if upload.content_type and upload.content_type.startswith("image/"):
        return upload.content_type

    return image_content_type_for_filename(upload.filename or "")


def is_image_upload(upload: UploadFile) -> bool:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in IMAGE_UPLOAD_EXTENSIONS:
        return False

    content_type = resolve_upload_content_type(upload)
    if not content_type.startswith("image/"):
        return False
    return content_type != "image/svg+xml" and content_type in IMAGE_CONTENT_TYPE_FORMATS


def validate_upload_image_bytes(image_bytes: bytes, filename: str, content_type: str) -> str:
    try:
        return validate_image_header_bytes(
            image_bytes,
            filename=filename,
            content_type=content_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
