import asyncio
import logging

from fastapi import APIRouter, HTTPException

from ..presets import (
    get_prompt_optimizer_settings,
    resolve_prompt_optimizer_api_key,
)
from ...integrations.prompt_optimizer_client import (
    OptimizerTimeoutError,
    PROMPT_OPTIMIZER_SYSTEM_PROMPT,
    probe_prompt_optimizer_endpoint,
    UpstreamOptimizerError,
    has_custom_prompt_optimizer_system_prompt,
    load_prompt_optimizer_system_prompt,
    optimize_prompt,
    save_prompt_optimizer_system_prompt,
    validate_optimizer_endpoint,
)
from ...schemas.models import (
    PromptOptimizeRequest,
    PromptOptimizeResponse,
    PromptOptimizerHealthResponse,
    PromptOptimizerSystemPromptRequest,
    PromptOptimizerSystemPromptResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _system_prompt_response(system_prompt: str) -> PromptOptimizerSystemPromptResponse:
    return PromptOptimizerSystemPromptResponse(
        system_prompt=system_prompt,
        default_system_prompt=PROMPT_OPTIMIZER_SYSTEM_PROMPT,
        customized=has_custom_prompt_optimizer_system_prompt(),
    )


@router.get(
    "/api/prompt/optimizer-system-prompt",
    response_model=PromptOptimizerSystemPromptResponse,
)
async def get_prompt_optimizer_system_prompt():
    system_prompt = await asyncio.to_thread(load_prompt_optimizer_system_prompt)
    return _system_prompt_response(system_prompt)


@router.post(
    "/api/prompt/optimizer-system-prompt",
    response_model=PromptOptimizerSystemPromptResponse,
)
async def update_prompt_optimizer_system_prompt(
    req: PromptOptimizerSystemPromptRequest,
):
    try:
        system_prompt = await asyncio.to_thread(
            save_prompt_optimizer_system_prompt,
            req.system_prompt,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _system_prompt_response(system_prompt)


@router.post("/api/prompt/optimize", response_model=PromptOptimizeResponse)
async def optimize_prompt_endpoint(req: PromptOptimizeRequest):
    settings = get_prompt_optimizer_settings()
    if not settings or not settings.get("enabled"):
        raise HTTPException(status_code=400, detail="Prompt optimizer is not enabled")

    api_url = str(settings.get("api_url", "")).strip()
    model = str(settings.get("model", "gpt-4o-mini")).strip() or "gpt-4o-mini"
    timeout_seconds = int(settings.get("timeout_seconds") or 60)
    try:
        api_key = resolve_prompt_optimizer_api_key(settings)
    except HTTPException as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail) from e

    if not api_url:
        raise HTTPException(status_code=400, detail="Prompt optimizer endpoint URL is not configured")
    if not model:
        raise HTTPException(status_code=400, detail="Prompt optimizer model is not configured")
    if not api_key:
        raise HTTPException(status_code=400, detail="Prompt optimizer API key is not configured")

    try:
        validated_api_url = validate_optimizer_endpoint(api_url)
        if validated_api_url:
            api_url = validated_api_url
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    try:
        optimized_prompt, model_used, duration_ms = await optimize_prompt(
            api_url=api_url,
            api_key=api_key,
            model=model,
            prompt=req.prompt,
            target_language=req.target_language,
            image_api_path=req.api_path,
            image_model=req.model,
            size=req.size,
            quality=req.quality,
            system_prompt=await asyncio.to_thread(load_prompt_optimizer_system_prompt),
            timeout_seconds=timeout_seconds,
        )
    except OptimizerTimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e)) from e
    except UpstreamOptimizerError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        logger.exception("Prompt optimizer unexpected error")
        raise HTTPException(status_code=502, detail="Prompt optimizer failed") from e

    return PromptOptimizeResponse(
        optimized_prompt=optimized_prompt,
        model=model_used,
        duration_ms=duration_ms,
    )


@router.post("/api/prompt/optimizer-health", response_model=PromptOptimizerHealthResponse)
async def prompt_optimizer_health():
    settings = get_prompt_optimizer_settings()
    if not settings or not settings.get("enabled"):
        return PromptOptimizerHealthResponse(
            status="error",
            message="Prompt optimizer is not enabled",
            model="",
            duration_ms=0,
            status_code=400,
        )

    api_url = str(settings.get("api_url", "")).strip()
    model = str(settings.get("model", "gpt-4o-mini")).strip() or "gpt-4o-mini"
    timeout_seconds = int(settings.get("timeout_seconds") or 60)
    try:
        api_key = resolve_prompt_optimizer_api_key(settings)
    except HTTPException as e:
        return PromptOptimizerHealthResponse(
            status="error",
            message=str(e.detail),
            model=model,
            duration_ms=0,
            status_code=e.status_code,
        )

    if not api_url:
        return PromptOptimizerHealthResponse(
            status="error",
            message="Prompt optimizer endpoint URL is not configured",
            model=model,
            duration_ms=0,
            status_code=400,
        )
    if not model:
        return PromptOptimizerHealthResponse(
            status="error",
            message="Prompt optimizer model is not configured",
            model="",
            duration_ms=0,
            status_code=400,
        )
    if not api_key:
        return PromptOptimizerHealthResponse(
            status="error",
            message="Prompt optimizer API key is not configured",
            model=model,
            duration_ms=0,
            status_code=400,
        )

    try:
        result = await probe_prompt_optimizer_endpoint(
            api_url=api_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )
    except ValueError as e:
        return PromptOptimizerHealthResponse(
            status="error",
            message=str(e),
            model=model,
            duration_ms=0,
            status_code=400,
        )
    except OptimizerTimeoutError as e:
        return PromptOptimizerHealthResponse(
            status="error",
            message=str(e),
            model=model,
            duration_ms=timeout_seconds * 1000,
            status_code=504,
        )
    except Exception as e:
        logger.exception("Prompt optimizer health probe unexpected error")
        return PromptOptimizerHealthResponse(
            status="error",
            message="Prompt optimizer health check failed",
            model=model,
            duration_ms=0,
            status_code=502,
        )

    return PromptOptimizerHealthResponse(**result)
