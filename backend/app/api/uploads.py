from pathlib import Path

from fastapi import HTTPException, Request, UploadFile
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

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
MULTIPART_FIELD_MAX_BYTES = 64 * 1024


def multipart_openapi_request_body(
    properties: dict[str, dict],
    *,
    required: list[str],
) -> dict:
    """Document manually parsed multipart bodies without enabling dependency parsing."""
    return {
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    }
                }
            },
        }
    }


async def parse_limited_multipart(
    request: Request,
    *,
    max_files: int,
    max_fields: int,
    allowed_file_fields: set[str],
    allow_urlencoded: bool = False,
    too_many_files_detail: str | None = None,
) -> FormData:
    content_type = request.headers.get("content-type", "")
    media_type = content_type.split(";", 1)[0].strip().lower()
    allowed_media_types = {"multipart/form-data"}
    if allow_urlencoded:
        allowed_media_types.add("application/x-www-form-urlencoded")
    if media_type not in allowed_media_types:
        raise HTTPException(status_code=415, detail="multipart/form-data is required")
    try:
        form = await request.form(
            max_files=max_files,
            max_fields=max_fields,
            max_part_size=MULTIPART_FIELD_MAX_BYTES,
        )
    except StarletteHTTPException as e:
        if too_many_files_detail and str(e.detail).startswith("Too many files"):
            raise HTTPException(status_code=400, detail=too_many_files_detail) from e
        raise
    field_count = 0
    try:
        for field_name, value in form.multi_items():
            if (
                isinstance(value, StarletteUploadFile)
                and field_name not in allowed_file_fields
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"Unexpected upload field: {field_name}",
                )
            if not isinstance(value, StarletteUploadFile):
                field_count += 1
                if field_count > max_fields:
                    raise HTTPException(status_code=400, detail="Too many form fields")
                if len(str(value).encode("utf-8")) > MULTIPART_FIELD_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="Form field is too large")
    except BaseException:
        await form.close()
        raise
    return form


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
