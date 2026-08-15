import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import aiohttp

from ..core import settings as config
from ..core.redaction import redact_sensitive_text
from ..core import validators as ssrf
from ..core.safe_connector import create_safe_connector
from ..core.utils import utc_now

logger = logging.getLogger(__name__)

WEBHOOK_USER_AGENT = "gpt-image-panel-webhook"
_WEBHOOK_RESPONSE_MAX_BYTES = 64 * 1024  # 64 KB


async def _drain_response_limited(
    response: aiohttp.ClientResponse, max_bytes: int
) -> None:
    """Read and discard response body up to *max_bytes*, then stop."""
    total = 0
    async for chunk in response.content.iter_chunked(8192):
        total += len(chunk)
        if total >= max_bytes:
            break


def build_webhook_payload(job: dict[str, Any]) -> dict[str, Any]:
    allowed_fields = {
        "job_id",
        "status",
        "stage",
        "operation",
        "id",
        "image_id",
        "image_url",
        "images",
        "size",
        "created_at",
        "started_at",
        "completed_at",
        "updated_at",
        "image_width",
        "image_height",
        "model",
        "quality",
        "output_format",
        "output_compression",
        "response_format",
        "n",
        "completed_count",
        "success_count",
        "failure_count",
        "api_path",
        "api_preset_name",
        "duration",
        "error",
    }
    payload = {key: job[key] for key in allowed_fields if key in job and job[key] is not None}
    payload["event"] = "image.job.finished"
    payload["delivered_at"] = utc_now()
    if str(job.get("status") or "") in {"partial_failure", "error", "upstream_error"}:
        payload["error_code"] = str(job.get("error_code") or "job_failed")
        payload["correlation_id"] = str(job.get("correlation_id") or uuid.uuid4().hex)
    return payload


def sign_webhook_body(body: bytes, timestamp: str) -> str:
    signed_payload = timestamp.encode("utf-8") + b"." + body
    return hmac.new(
        config.WEBHOOK_SIGNING_SECRET.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()


async def deliver_webhook(webhook_url: str, job: dict[str, Any]):
    try:
        await ssrf.validate_webhook_url_async(webhook_url, config.WEBHOOK_HOST_ALLOWLIST)
    except ValueError as error:
        logger.warning(
            "Webhook URL rejected before delivery: job_id=%s error=%s",
            job.get("job_id"),
            error,
        )
        return

    payload = build_webhook_payload(job)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    headers = {
        "Content-Type": "application/json",
        "User-Agent": WEBHOOK_USER_AGENT,
        "X-Webhook-Event": "image.job.finished",
        "X-Webhook-Job-Id": str(job.get("job_id") or ""),
        "X-Webhook-Timestamp": timestamp,
    }
    signature = sign_webhook_body(body, timestamp)
    if signature:
        headers["X-Webhook-Signature"] = f"sha256={signature}"

    attempts = max(config.WEBHOOK_MAX_ATTEMPTS, 1)
    timeout = aiohttp.ClientTimeout(total=config.WEBHOOK_TIMEOUT_SECONDS)
    last_error = None

    async with aiohttp.ClientSession(
        timeout=timeout, connector=create_safe_connector()
    ) as session:
        for attempt in range(1, attempts + 1):
            try:
                async with session.post(
                    webhook_url,
                    data=body,
                    headers=headers,
                    allow_redirects=False,
                ) as response:
                    ssrf.validate_response_peer_ip(response, "Webhook")
                    await _drain_response_limited(response, _WEBHOOK_RESPONSE_MAX_BYTES)
                    if 200 <= response.status < 300:
                        logger.info(
                            "Webhook delivered: job_id=%s status=%s attempt=%s",
                            job.get("job_id"),
                            response.status,
                            attempt,
                        )
                        return
                    last_error = f"HTTP {response.status}"
            except Exception as error:
                last_error = redact_sensitive_text(str(error) or error.__class__.__name__)

            if attempt < attempts:
                await asyncio.sleep(min(2 ** (attempt - 1), 4))

    logger.warning(
        "Webhook delivery failed: job_id=%s attempts=%s error=%s",
        job.get("job_id"),
        attempts,
        last_error,
    )
