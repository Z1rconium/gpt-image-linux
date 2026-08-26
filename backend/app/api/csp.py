import re
import secrets
from pathlib import Path

from fastapi.responses import HTMLResponse


def build_content_security_policy(script_nonce: str | None = None) -> str:
    script_sources = ["'self'"]
    script_elem_sources = ["'self'"]
    if script_nonce:
        nonce_source = f"'nonce-{script_nonce}'"
        script_sources.append(nonce_source)
        script_elem_sources.append(nonce_source)

    turnstile = "https://challenges.cloudflare.com"

    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            f"script-src {' '.join([*script_sources, turnstile])}",
            f"script-src-elem {' '.join([*script_elem_sources, turnstile])}",
            "script-src-attr 'none'",
            "style-src 'self'",
            "style-src-attr 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            f"connect-src 'self' {turnstile}",
            f"frame-src {turnstile}",
        ]
    )


CONTENT_SECURITY_POLICY = build_content_security_policy()
SCRIPT_TAG_RE = re.compile(r"<script(?P<attrs>[^>]*)>", re.IGNORECASE)


def add_script_nonce(html: str, nonce: str) -> str:
    def replace_script_tag(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        if re.search(r"\snonce\s*=", attrs, flags=re.IGNORECASE):
            return match.group(0)
        return f'<script nonce="{nonce}"{attrs}>'

    return SCRIPT_TAG_RE.sub(replace_script_tag, html)


def frontend_index_response(index_path: Path) -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    html = add_script_nonce(index_path.read_text(encoding="utf-8"), nonce)
    response = HTMLResponse(html)
    response.headers["Content-Security-Policy"] = build_content_security_policy(
        script_nonce=nonce
    )
    response.headers["Cache-Control"] = "no-cache"
    return response
