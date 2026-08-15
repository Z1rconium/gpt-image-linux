import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile
from starlette.datastructures import FormData
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..edit_limits import MAX_EDIT_SOURCE_IMAGES
from ...services.gallery_archive_shared import max_upload_bytes
from ...services.job_queue import (
    EditImageSource,
    build_edit_request_from_form,
    queue_edit_job,
)
from ..uploads import (
    is_image_upload,
    multipart_openapi_request_body,
    parse_limited_multipart,
    resolve_upload_content_type,
    validate_upload_image_bytes,
)
from ...core import settings as config
from ...repositories.gallery.queries import get_gallery_entry
from ...repositories.image_files import (
    image_content_type_for_filename,
    safe_image_path,
    validate_image_file,
)
from ...schemas.generation import EditRequest, GenerateJobResponse
from ...services.blocking import run_db_operation, run_image_operation


router = APIRouter()


EDIT_SOURCE_SNIFF_BYTES = 512
EDIT_SOURCE_CHUNK_BYTES = 1024 * 1024
MAX_GALLERY_EDIT_UPLOAD_IMAGES = MAX_EDIT_SOURCE_IMAGES - 1
EDIT_MULTIPART_PROPERTIES = {
    "image": {
        "type": "array",
        "items": {"type": "string", "format": "binary"},
        "maxItems": MAX_EDIT_SOURCE_IMAGES,
    },
    "prompt": {"type": "string"},
    "size": {"type": "string", "default": "auto"},
    "model": {"type": "string", "default": ""},
    "n": {"type": "integer", "default": 1},
    "quality": {"type": "string", "default": "auto"},
    "output_format": {"type": "string", "default": "png"},
    "output_compression": {"type": "integer", "nullable": True},
    "response_format": {"type": "string", "nullable": True},
    "webhook_url": {"type": "string", "nullable": True},
}
GALLERY_EDIT_MULTIPART_PROPERTIES = {
    **EDIT_MULTIPART_PROPERTIES,
    "image": {
        "type": "array",
        "items": {"type": "string", "format": "binary"},
        "maxItems": MAX_GALLERY_EDIT_UPLOAD_IMAGES,
    },
}


def _form_int(
    form: FormData,
    name: str,
    default: int | None,
) -> int | None:
    value = form.get(name)
    if value in (None, ""):
        return default
    try:
        return int(str(value))
    except (TypeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"{name} must be an integer") from e


def edit_request_from_form(form: FormData) -> EditRequest:
    prompt = str(form.get("prompt") or "")
    if not prompt:
        raise HTTPException(status_code=422, detail="prompt is required")
    n = _form_int(form, "n", 1)
    if n is None:
        n = 1
    return build_edit_request_from_form(
        prompt=prompt,
        size=str(form.get("size") or "auto"),
        model=str(form.get("model") or ""),
        n=n,
        quality=str(form.get("quality") or "auto"),
        output_format=str(form.get("output_format") or "png"),
        output_compression=_form_int(form, "output_compression", None),
        response_format=(
            str(form.get("response_format"))
            if form.get("response_format") not in (None, "")
            else None
        ),
        webhook_url=(
            str(form.get("webhook_url"))
            if form.get("webhook_url") not in (None, "")
            else None
        ),
    )


def create_edit_source_temp_path(filename: str) -> tuple[int, Path]:
    suffix = Path(filename or "").suffix.lower() or ".img"
    temp_dir = Path(config.DATA_DIR) / "edit-sources"
    temp_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix="edit-source-",
        suffix=suffix,
        dir=temp_dir,
    )
    return fd, Path(temp_name)


def validate_edit_source_header(
    image_header: bytes,
    byte_size: int,
    filename: str,
    content_type: str,
    *,
    empty_detail: str,
    too_large_detail: str,
):
    if byte_size == 0:
        raise HTTPException(status_code=400, detail=empty_detail)
    if byte_size > max_upload_bytes():
        raise HTTPException(status_code=400, detail=too_large_detail)
    validate_upload_image_bytes(image_header, filename, content_type)


def validate_edit_source_file(path: Path, filename: str, content_type: str):
    try:
        validate_image_file(
            path,
            filename=filename,
            content_type=content_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


def copy_edit_source_file_to_temp(
    path: Path,
    filename: str,
    content_type: str,
    *,
    empty_detail: str,
    too_large_detail: str,
    read_error_detail: str,
) -> EditImageSource:
    try:
        with path.open("rb") as source:
            return copy_edit_source_stream_to_temp(
                source,
                filename,
                content_type,
                empty_detail=empty_detail,
                too_large_detail=too_large_detail,
                read_error_detail=read_error_detail,
            )
    except HTTPException:
        raise
    except OSError as e:
        raise HTTPException(status_code=500, detail=read_error_detail) from e


def copy_edit_source_stream_to_temp(
    source,
    filename: str,
    content_type: str,
    *,
    empty_detail: str,
    too_large_detail: str,
    read_error_detail: str,
) -> EditImageSource:
    fd, temp_path = create_edit_source_temp_path(filename)
    total = 0
    header = bytearray()

    try:
        source.seek(0)
        with os.fdopen(fd, "wb") as target:
            while True:
                chunk = source.read(EDIT_SOURCE_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_upload_bytes():
                    raise HTTPException(status_code=400, detail=too_large_detail)
                if len(header) < EDIT_SOURCE_SNIFF_BYTES:
                    header.extend(chunk[: EDIT_SOURCE_SNIFF_BYTES - len(header)])
                target.write(chunk)
    except HTTPException:
        temp_path.unlink(missing_ok=True)
        raise
    except OSError as e:
        temp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=read_error_detail) from e
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    try:
        validate_edit_source_header(
            bytes(header),
            total,
            filename,
            content_type,
            empty_detail=empty_detail,
            too_large_detail=too_large_detail,
        )
        validate_edit_source_file(temp_path, filename, content_type)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    return EditImageSource(temp_path, total, filename, content_type)


def cleanup_edit_sources(sources: list[EditImageSource]):
    for source in sources:
        source.temp_path.unlink(missing_ok=True)


def validate_edit_source_count(sources: list[EditImageSource]):
    if len(sources) > MAX_EDIT_SOURCE_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_EDIT_SOURCE_IMAGES} edit source images are supported.",
        )


async def read_upload_edit_source(image: UploadFile) -> EditImageSource:
    if not is_image_upload(image):
        raise HTTPException(status_code=400, detail="Upload must be an image file.")

    image_content_type = resolve_upload_content_type(image)
    filename = image.filename or "image.png"
    return await run_image_operation(
        copy_edit_source_stream_to_temp,
        image.file,
        filename,
        image_content_type,
        empty_detail="Uploaded image is empty.",
        too_large_detail=(
            f"Uploaded image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB."
        ),
        read_error_detail="Failed to read uploaded image",
        metric_name="copy_validate_edit_upload",
    )


async def read_upload_edit_sources(
    form: FormData,
    *,
    max_sources: int = MAX_EDIT_SOURCE_IMAGES,
) -> list[EditImageSource]:
    uploads: list[UploadFile] = []
    for field_name in ("image", "image[]"):
        for value in form.getlist(field_name):
            if isinstance(value, StarletteUploadFile):
                uploads.append(value)

    if len(uploads) > max_sources:
        raise HTTPException(
            status_code=400,
            detail=f"At most {max_sources} uploaded edit source images are supported.",
        )

    sources: list[EditImageSource] = []
    results = await asyncio.gather(
        *(read_upload_edit_source(upload) for upload in uploads),
        return_exceptions=True,
    )
    sources = [result for result in results if isinstance(result, EditImageSource)]
    error = next(
        (result for result in results if isinstance(result, BaseException)),
        None,
    )
    if error is not None:
        cleanup_edit_sources(sources)
        raise error
    return sources


async def read_gallery_edit_source(image_id: str) -> EditImageSource:
    entry = await run_db_operation(
        get_gallery_entry,
        image_id,
        metric_name="get_gallery_edit_source",
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")

    path = safe_image_path(entry.filename)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Gallery image file not found")

    image_content_type = image_content_type_for_filename(path.name)

    return await run_image_operation(
        copy_edit_source_file_to_temp,
        path,
        path.name,
        image_content_type,
        empty_detail="Gallery image is empty",
        too_large_detail=f"Gallery image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB.",
        read_error_detail="Failed to read gallery image",
        metric_name="copy_validate_gallery_edit_source",
    )


@router.post(
    "/api/edits",
    response_model=GenerateJobResponse,
    status_code=202,
    openapi_extra=multipart_openapi_request_body(
        EDIT_MULTIPART_PROPERTIES,
        required=["image", "prompt"],
    ),
)
async def edit_image(
    request: Request,
):
    form = await parse_limited_multipart(
        request,
        max_files=MAX_EDIT_SOURCE_IMAGES,
        max_fields=16,
        allowed_file_fields={"image", "image[]"},
        too_many_files_detail=(
            f"At most {MAX_EDIT_SOURCE_IMAGES} edit source images are supported."
        ),
    )
    try:
        req = edit_request_from_form(form)
        sources = await read_upload_edit_sources(form)
        if not sources:
            raise HTTPException(status_code=422, detail="Upload image is required.")
        validate_edit_source_count(sources)
        try:
            return await queue_edit_job(
                req=req,
                image_sources=sources,
            )
        except BaseException:
            cleanup_edit_sources(sources)
            raise
    finally:
        await form.close()


@router.post(
    "/api/edits/from-gallery/{image_id}",
    response_model=GenerateJobResponse,
    status_code=202,
    openapi_extra=multipart_openapi_request_body(
        GALLERY_EDIT_MULTIPART_PROPERTIES,
        required=["prompt"],
    ),
)
async def edit_image_from_gallery(
    request: Request,
    image_id: str,
):
    form = await parse_limited_multipart(
        request,
        max_files=MAX_GALLERY_EDIT_UPLOAD_IMAGES,
        max_fields=16,
        allowed_file_fields={"image", "image[]"},
        allow_urlencoded=True,
        too_many_files_detail=(
            f"At most {MAX_GALLERY_EDIT_UPLOAD_IMAGES} uploaded edit source images "
            "are supported when editing from the gallery."
        ),
    )
    try:
        req = edit_request_from_form(form)
        upload_sources = await read_upload_edit_sources(
            form,
            max_sources=MAX_GALLERY_EDIT_UPLOAD_IMAGES,
        )
        try:
            gallery_source = await read_gallery_edit_source(image_id)
        except BaseException:
            cleanup_edit_sources(upload_sources)
            raise
        sources = [gallery_source, *upload_sources]
        try:
            validate_edit_source_count(sources)
        except BaseException:
            cleanup_edit_sources(sources)
            raise
        try:
            return await queue_edit_job(
                req=req,
                image_sources=sources,
            )
        except BaseException:
            cleanup_edit_sources(sources)
            raise
    finally:
        await form.close()
