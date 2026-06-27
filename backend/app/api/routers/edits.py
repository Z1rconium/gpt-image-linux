import asyncio
import mimetypes
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..edit_limits import MAX_EDIT_SOURCE_IMAGES
from ..gallery_archive import max_upload_bytes
from ..jobs import EditImageSource, build_edit_request_from_form, queue_edit_job
from ..uploads import (
    IMAGE_UPLOAD_CONTENT_TYPES,
    is_image_upload,
    resolve_upload_content_type,
    validate_upload_image_bytes,
)
from ...core import settings as config
from ...repositories import storage
from ...schemas.models import EditRequest, GenerateJobResponse


router = APIRouter()


EDIT_SOURCE_SNIFF_BYTES = 512
EDIT_SOURCE_CHUNK_BYTES = 1024 * 1024


def edit_request_from_form(
    prompt: str = Form(...),
    size: str = Form("auto"),
    model: str = Form(""),
    n: int = Form(1),
    quality: str = Form("auto"),
    output_format: str = Form("png"),
    output_compression: int | None = Form(None),
    response_format: str | None = Form(None),
    webhook_url: str | None = Form(None),
) -> EditRequest:
    return build_edit_request_from_form(
        prompt=prompt,
        size=size,
        model=model,
        n=n,
        quality=quality,
        output_format=output_format,
        output_compression=output_compression,
        response_format=response_format,
        webhook_url=webhook_url,
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
        storage.validate_image_file(
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
    fd, temp_path = create_edit_source_temp_path(filename)
    total = 0
    header = bytearray()

    try:
        with os.fdopen(fd, "wb") as target, path.open("rb") as source:
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
    fd, temp_path = create_edit_source_temp_path(filename)
    total = 0
    header = bytearray()

    try:
        with os.fdopen(fd, "wb") as target:
            while True:
                chunk = await image.read(EDIT_SOURCE_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_upload_bytes():
                    raise HTTPException(
                        status_code=400,
                        detail=f"Uploaded image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB.",
                    )
                if len(header) < EDIT_SOURCE_SNIFF_BYTES:
                    header.extend(chunk[: EDIT_SOURCE_SNIFF_BYTES - len(header)])
                target.write(chunk)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    try:
        validate_edit_source_header(
            bytes(header),
            total,
            filename,
            image_content_type,
            empty_detail="Uploaded image is empty.",
            too_large_detail=f"Uploaded image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB.",
        )
        validate_edit_source_file(temp_path, filename, image_content_type)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise

    return EditImageSource(temp_path, total, filename, image_content_type)


async def read_upload_edit_sources(request: Request) -> list[EditImageSource]:
    form = await request.form()
    uploads: list[UploadFile] = []
    for field_name in ("image", "image[]"):
        for value in form.getlist(field_name):
            if isinstance(value, StarletteUploadFile):
                uploads.append(value)

    if len(uploads) > MAX_EDIT_SOURCE_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"At most {MAX_EDIT_SOURCE_IMAGES} edit source images are supported.",
        )

    sources: list[EditImageSource] = []
    try:
        for upload in uploads:
            sources.append(await read_upload_edit_source(upload))
    except BaseException:
        cleanup_edit_sources(sources)
        raise
    return sources


async def read_gallery_edit_source(image_id: str) -> EditImageSource:
    entry = await asyncio.to_thread(storage.get_gallery_entry, image_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")

    path = await asyncio.to_thread(storage.safe_image_path, entry.filename)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Gallery image file not found")

    image_content_type = (
        mimetypes.guess_type(path.name)[0]
        or IMAGE_UPLOAD_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    )

    return await asyncio.to_thread(
        copy_edit_source_file_to_temp,
        path,
        path.name,
        image_content_type,
        empty_detail="Gallery image is empty",
        too_large_detail=f"Gallery image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB.",
        read_error_detail="Failed to read gallery image",
    )


@router.post("/api/edits", response_model=GenerateJobResponse, status_code=202)
async def edit_image(
    request: Request,
    req: EditRequest = Depends(edit_request_from_form),
):
    sources = await read_upload_edit_sources(request)
    if not sources:
        raise HTTPException(status_code=422, detail="Upload image is required.")
    validate_edit_source_count(sources)
    try:
        return queue_edit_job(
            req=req,
            image_sources=sources,
        )
    except BaseException:
        cleanup_edit_sources(sources)
        raise


@router.post(
    "/api/edits/from-gallery/{image_id}",
    response_model=GenerateJobResponse,
    status_code=202,
)
async def edit_image_from_gallery(
    request: Request,
    image_id: str,
    req: EditRequest = Depends(edit_request_from_form),
):
    upload_sources = await read_upload_edit_sources(request)
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
        return queue_edit_job(
            req=req,
            image_sources=sources,
        )
    except BaseException:
        cleanup_edit_sources(sources)
        raise
