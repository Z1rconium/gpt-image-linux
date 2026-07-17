import aiohttp
import asyncio
import base64
import json
import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from ...core import settings as config
from ...core.api_paths import (
    CHAT_COMPLETIONS_API_PATH,
    RESPONSES_API_PATH,
    build_upstream_url,
    normalize_api_path,
)
from ...core.observability import observe_job_stage
from ...core import validators as ssrf
from ...repositories.gallery.mutations import add_to_gallery_async
from ...repositories.image_files import (
    detect_image_format,
    generate_image_id,
    validate_image_bytes,
)
from ...schemas.gallery import GalleryEntry
from ...schemas.generation import EditRequest, GenerateRequest
from ..session_pool import TIMEOUT_PROBE, TIMEOUT_UPSTREAM, get_pool

ProgressCallback = Callable[[str, str], None]
logger = logging.getLogger(__name__)


from .errors import *
from .payloads import *
from .transport import *

async def save_gallery_entries_from_upstream_data(
    *,
    download_session: aiohttp.ClientSession,
    data: list[dict[str, Any]],
    response_preview: str,
    payload: GenerateRequest,
    format_extension: str,
    gallery_metadata: dict[str, Any],
    save_message: str,
    progress: ProgressCallback | None,
) -> list[GalleryEntry]:
    data = validate_upstream_image_data(data, payload.n)
    max_bytes = config.MAX_FILE_SIZE_MB * 1024 * 1024
    total = len(data)

    async def process_one(image_index: int, image_data: dict) -> GalleryEntry:
        transfer_stage, transfer_message = get_image_transfer_stage(image_data)
        if progress:
            progress(
                transfer_stage,
                f"{transfer_message} ({image_index + 1}/{total})",
            )
        with observe_job_stage("download_decode"):
            image_bytes = await extract_image_bytes(
                download_session,
                image_data,
                response_preview,
                max_bytes,
            )
        try:
            if progress:
                progress(
                    "validating_image_bytes",
                    f"Validating decoded image ({image_index + 1}/{total})",
                )
            with observe_job_stage("validate"):
                if len(image_bytes) > max_bytes:
                    raise UpstreamImageDownloadError(
                        f"Image too large: {len(image_bytes)} bytes (max {max_bytes})"
                    )

                detected_format = detect_image_format(image_bytes)
                detected_extension = DETECTED_FORMAT_EXTENSIONS.get(
                    detected_format or "",
                    format_extension,
                )
                image_id = generate_image_id()
                filename = f"{image_id}.{detected_extension}"
                validate_generated_image_bytes(image_bytes, filename)
            entry_metadata = {**gallery_metadata}
            if detected_format:
                entry_metadata["output_format"] = detected_format

            if progress:
                progress(
                    "saving_images",
                    f"{save_message} ({image_index + 1}/{total})",
                )
            entry = await add_to_gallery_async(
                image_bytes=image_bytes,
                image_id=image_id,
                prompt=payload.prompt,
                size=payload.size,
                filename=filename,
                metadata=entry_metadata,
            )
            return entry
        finally:
            del image_bytes

    if total <= 1:
        entries = [await process_one(0, data[0])]
    else:
        sem = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

        async def bounded(idx: int, img: dict) -> GalleryEntry:
            async with sem:
                return await process_one(idx, img)

        entries = list(
            await asyncio.gather(*(bounded(i, d) for i, d in enumerate(data)))
        )
    return entries


async def call_image_generation_api(
    api_url: str,
    api_key: str,
    api_path: str,
    payload: GenerateRequest,
    api_preset_name: str | None = None,
    progress: ProgressCallback | None = None,
    socks5_proxy: str | None = None,
) -> list[GalleryEntry]:
    api_path = normalize_api_path(api_path)
    upstream_url = build_upstream_url(api_url, api_path)

    _warn_if_socks5_upstream_resolves_private(upstream_url, socks5_proxy)
    ssrf.validate_upstream_url(upstream_url, config.UPSTREAM_HOST_ALLOWLIST)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "opencode",
    }

    if api_path == RESPONSES_API_PATH:
        if progress:
            progress("building_responses_payload", "Building Responses API payload")
        request_data = build_responses_request_data(payload)
    elif api_path == CHAT_COMPLETIONS_API_PATH:
        if progress:
            progress(
                "building_chat_completions_payload",
                "Building Chat Completions API payload",
            )
        request_data = build_chat_completions_request_data(payload)
    else:
        if progress:
            progress("building_generation_payload", "Building image generation payload")
        request_data = _build_image_params(payload)

    format_info = get_output_format_info(payload.output_format)
    gallery_metadata = build_gallery_metadata(payload, api_path, api_preset_name)

    pool = get_pool()
    upstream_session = pool.get(timeout_kind=TIMEOUT_UPSTREAM, socks5_proxy=socks5_proxy)
    download_session = pool.get(timeout_kind=TIMEOUT_UPSTREAM)

    if progress:
        progress("waiting_for_api", "Waiting for upstream API response")
    with observe_job_stage("upstream_wait"):
        async with upstream_session.post(
            upstream_url,
            json=request_data,
            headers=headers,
            allow_redirects=False,
        ) as resp:
            if not socks5_proxy:
                ssrf.validate_response_peer_ip(resp, "Upstream API")
            if api_path == CHAT_COMPLETIONS_API_PATH:
                result, response_text = await parse_upstream_chat_completion_response(
                    resp, api_path, progress
                )
            else:
                result, response_text = await parse_upstream_json_response(
                    resp, api_path, progress
                )

    if api_path == RESPONSES_API_PATH:
        if progress:
            progress(
                "extracting_response_image_output",
                "Extracting image_generation_call output",
            )
        data = extract_response_image_results(result)
    elif api_path == CHAT_COMPLETIONS_API_PATH:
        if progress:
            progress(
                "extracting_chat_completion_image_output",
                "Extracting Chat Completions image output",
            )
        data = extract_chat_completion_image_results(result)
    else:
        if progress:
            progress("extracting_generation_data", "Extracting image data array")
        data = result.get("data", [])
    data = validate_upstream_image_data(data, payload.n)
    if not data:
        text_preview = response_text[:200] if isinstance(response_text, str) else str(response_text)[:200]
        raise UpstreamApiError(f"No image data in upstream response: {text_preview}")

    response_preview = response_text[:200]
    del response_text
    del result
    entries = await save_gallery_entries_from_upstream_data(
        download_session=download_session,
        data=data,
        response_preview=response_preview,
        payload=payload,
        format_extension=format_info["extension"],
        gallery_metadata=gallery_metadata,
        save_message="Saving generated images",
        progress=progress,
    )

    return entries


async def call_image_edit_api(
    api_url: str,
    api_key: str,
    payload: EditRequest,
    image_sources: Sequence[ImageEditSource],
    api_preset_name: str | None = None,
    progress: ProgressCallback | None = None,
    socks5_proxy: str | None = None,
) -> list[GalleryEntry]:
    if not image_sources:
        raise UpstreamApiError("At least one edit source image is required")

    api_path = "/v1/images/edits"
    upstream_url = build_upstream_url(api_url, api_path)

    _warn_if_socks5_upstream_resolves_private(upstream_url, socks5_proxy)
    ssrf.validate_upstream_url(upstream_url, config.UPSTREAM_HOST_ALLOWLIST)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "opencode",
    }
    format_info = get_output_format_info(payload.output_format)
    gallery_metadata = build_gallery_metadata(payload, api_path, api_preset_name)

    if progress:
        progress("building_edit_form", "Building multipart edit request")
    image_files = []
    try:
        form = aiohttp.FormData()
        image_field_name = "image" if len(image_sources) == 1 else "image[]"
        for source in image_sources:
            image_file = source.temp_path.open("rb")
            image_files.append(image_file)
            form.add_field(
                image_field_name,
                image_file,
                filename=source.filename or "image.png",
                content_type=source.content_type or "application/octet-stream",
            )
        for key, value in _build_image_params(payload).items():
            form.add_field(key, str(value))

        pool = get_pool()
        upstream_session = pool.get(timeout_kind=TIMEOUT_UPSTREAM, socks5_proxy=socks5_proxy)
        if progress:
            upload_message = (
                "Uploading source image and edit parameters"
                if len(image_sources) == 1
                else "Uploading source images and edit parameters"
            )
            progress("uploading_edit_image", upload_message)
        with observe_job_stage("upstream_wait"):
            async with upstream_session.post(
                upstream_url,
                data=form,
                headers=headers,
                allow_redirects=False,
            ) as resp:
                if not socks5_proxy:
                    ssrf.validate_response_peer_ip(resp, "Upstream API")
                result, response_text = await parse_upstream_json_response(
                    resp, api_path, progress
                )

        if progress:
            progress("extracting_edit_data", "Extracting edited image data array")
        data = validate_upstream_image_data(result.get("data", []), payload.n)
        if not data:
            raise UpstreamApiError(f"No image data in upstream response: {response_text[:200]}")

        response_preview = response_text[:200]
        del response_text
        del result
        download_session = pool.get(timeout_kind=TIMEOUT_UPSTREAM)
        return await save_gallery_entries_from_upstream_data(
            download_session=download_session,
            data=data,
            response_preview=response_preview,
            payload=payload,
            format_extension=format_info["extension"],
            gallery_metadata=gallery_metadata,
            save_message="Saving edited images",
            progress=progress,
        )
    finally:
        for image_file in image_files:
            image_file.close()
