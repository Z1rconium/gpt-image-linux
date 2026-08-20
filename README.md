<div align="center">
  <br />
  <img src="frontend/static/favicon.svg" alt="GPT Image Panel logo" width="128" height="128" />

  <h1>GPT Image Panel</h1>

  <hr />

  <p>
    <strong>Self-hosted GPT-compatible image generation and editing panel.</strong>
  </p>

  <p>
    <a href="#english">English</a> ·
    <a href="./README.zh-CN.md">简体中文</a> ·
    <a href="./README.zh-TW.md">繁體中文</a>
  </p>

  <p>
    <img alt="CI passing" src="https://img.shields.io/badge/CI-passing-2cc653?logo=github&logoColor=white" />
    <img alt="Release v1.3.3" src="https://img.shields.io/badge/release-v1.3.3-0e8dcc" />
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" />
    <img alt="Node.js 24" src="https://img.shields.io/badge/Node.js-24-339933?logo=node.js&logoColor=white" />
    <img alt="FastAPI 0.115+" src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" />
    <img alt="SvelteKit 2" src="https://img.shields.io/badge/SvelteKit-2-FF3E00?logo=svelte&logoColor=white" />
    <img alt="License CC BY-NC 4.0" src="https://img.shields.io/badge/License-CC_BY--NC_4.0-6f42c1" />
    <img alt="Image GHCR" src="https://img.shields.io/badge/GHCR-gpt--image--linux-1f6f8b?logo=github&logoColor=white" />
  </p>
</div>

<a id="english"></a>

## Overview

GPT Image Panel is a lightweight web UI for image generation, image editing, gallery management, and local persistence. It connects to an external GPT-compatible image API and stores images plus metadata on your own server.

This project is only a self-hosted control panel. It does not provide, proxy, resell, or modify any upstream model/API service. Generation capability, billing, account permissions, content policy, and model behavior all come from the upstream provider you configure.

## Features

- Image generation through `/v1/images/generations`, `/v1/responses`, or OpenAI-compatible `/v1/chat/completions`.
- Image editing through `/v1/images/edits`, including uploaded references and gallery-source edits.
- API presets with base URL/path/key, default model, response format, health checks, SOCKS5 proxy, webhook, and env-ref secret support.
- Web-managed Overall Config for selected runtime settings, with env/default/override sources and restart/build-only badges.
- Prompt helper tags, reusable prompt snippets, optional server-side prompt optimizer, and an AI Assistant subsystem for prompt rewrites/checks/variants, parameter recommendations, job diagnosis, edit planning, and gallery image analysis.
- SQLite-backed job queue with SSE progress, cancellation, retry/reuse, persisted history, stage timing metadata, and shared generation/edit concurrency limits.
- Local gallery with cursor pagination, search/filtering, favorites, lightbox navigation, selection-token batch actions, ZIP import/export, thumbnails, byte-size metadata, and async export/import jobs.
- Optional Cloudflare R2 gallery backup sync; local SQLite/images remain the source of truth.
- Access-key gate, IP/Host allowlists, trusted proxy-header support, CSRF origin checks, CSP nonce injection, version checks, and optional JSON/Prometheus metrics.

## Architecture

- Backend: FastAPI under `backend/app/`; ASGI entrypoint is `backend.app.main:app`.
- Frontend: SvelteKit static app under `frontend/`; production backend serves `frontend/build/`.
- Runtime storage: generated images under `images/`, thumbnails under `images/thumbs/`, SQLite data under `data/app.sqlite3`, and logs under `data/logs/` by default.
- Multi-worker coordination: queued jobs, background leases, SSE slots, and scheduler ownership use SQLite leases. Image and thumbnail file writes/deletes use process-local locks only, and tolerate cross-process races through UUID filenames, atomic `Path.replace()`, and orphan-file GC TTL cleanup.
- Public API routing: `backend/app/api/contract_app.py`.
- DTOs: `backend/app/schemas/`.
- Persistence: `backend/app/repositories/`.
- Upstream API integration: `backend/app/integrations/`.
- Runtime config: `backend/app/core/settings.py`, `backend/app/core/overall_config.py`, `.env.example`, and `docker-compose.yml`.

## Tech Stack

- Python 3.11+
- FastAPI
- Granian
- aiohttp
- aiohttp-socks
- boto3
- SQLite
- Pydantic v2
- Pillow
- zipstream-ng
- SvelteKit
- TypeScript
- Tailwind CSS
- Playwright
- pytest

## Project Structure

```text
backend/
  app/
    api/
    core/
    integrations/
    repositories/
    schemas/
    services/
  tests/
frontend/
  src/
    lib/
    routes/
  tests/
deploy/
  nginx.conf
images/
data/
Dockerfile
docker-compose.yml
.env.example
requirements.txt
backend/requirements-dev.txt
package.json
```

## Quick Start

### Docker Compose

```bash
cp .env.example .env
# edit .env: set ACCESS_KEY and any default upstream API values you want
# this example uses plain HTTP on loopback, so disable Secure cookies
ACCESS_COOKIE_SECURE=false docker-compose up -d --force-recreate
```

Open `http://127.0.0.1:9090`.

This local HTTP example requires `ACCESS_COOKIE_SECURE=false`; keep it `true` when serving the panel over HTTPS. By default, `ACCESS_KEY` is required. For local-only testing, unset `ACCESS_KEY` and set `ALLOW_UNAUTHENTICATED=true`; this makes every non-health API route accessible.

### Docker

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 127.0.0.1:9090:9090 \
  -e ACCESS_KEY=change-me \
  -e ACCESS_COOKIE_SECURE=false \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

If Docker Hub is slow or blocked:

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

### Caddy Reverse Proxy

When Caddy and the application run on the same host, use a placeholder hostname and keep port `9090` bound to loopback:

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:9090
}
```

For an HTTPS deployment, set the matching application origin and Host allowlist in `.env`:

```dotenv
PUBLIC_ORIGIN=https://panel.example.com
ALLOWED_HOSTS=panel.example.com
ACCESS_COOKIE_SECURE=true
```

The basic proxy is recommended when there is only one upstream. If active health checks are required, first confirm that `/health` returns `200` with the same `Host` header, then use the plural `health_headers` block:

```bash
curl -i -H 'Host: panel.example.com' http://127.0.0.1:9090/health
```

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:9090 {
        health_uri /health
        health_interval 15s
        health_timeout 3s
        health_status 200

        health_headers {
            Host panel.example.com
        }
    }
}
```

A health-check response outside the configured status range marks the upstream unhealthy. With only one upstream, this can make requests fail until the check succeeds again. Validate and reload Caddy after changes:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy automatic HTTPS requires the hostname to resolve to the server and inbound ports `80` and `443` to be reachable. When using a CDN proxy such as Cloudflare, ensure its edge certificate explicitly covers the complete hostname, especially for multi-label subdomains; otherwise browsers may report `ERR_SSL_VERSION_OR_CIPHER_MISMATCH` before traffic reaches Caddy. If Caddy runs in a container, `127.0.0.1` refers to that container, so use the application service name or another reachable container-network address instead.

### Deployment Troubleshooting

1. **Container exits on startup with `SecretRegistryError: credentials require a non-empty startup host allowlist`**
   - **Cause**: `DEFAULT_API_KEY` is configured in `.env`, but `UPSTREAM_HOST_ALLOWLIST` is missing or empty.
   - **Fix**: Add the raw hostname of `DEFAULT_API_URL` to `UPSTREAM_HOST_ALLOWLIST` in `.env` (e.g. `UPSTREAM_HOST_ALLOWLIST=api.openai.com` or `UPSTREAM_HOST_ALLOWLIST=cf.api.fan`).
2. **Browser reports `400 Bad Request: Host is not allowed`**
   - **Cause**: The incoming `Host` header does not match `ALLOWED_HOSTS` or `PUBLIC_ORIGIN` in `.env` (often due to domain typos or failing to recreate the container after editing `.env`).
   - **Fix**: Ensure `PUBLIC_ORIGIN=https://panel.example.com` and `ALLOWED_HOSTS=panel.example.com` match the exact domain. Always run `docker compose up -d --force-recreate` after modifying `.env`.
3. **Cannot log in or login loop under direct plain HTTP (no reverse proxy/SSL)**
   - **Cause**: By default `ACCESS_COOKIE_SECURE=true` requires HTTPS; browsers reject storing or sending `Secure` cookies over plain HTTP.
   - **Fix**: For direct HTTP testing, set `ACCESS_COOKIE_SECURE=false` in `.env`. Switch back to `true` once behind an HTTPS reverse proxy.
4. **Clicking "Save Preset" in Web UI fails or drawer does not close**
   - **Cause**:
     - Saving literal plaintext API keys to SQLite via the UI is prohibited by default. Set `ALLOW_PLAINTEXT_SECRETS=true` in `.env` and restart the container if you need to paste raw API keys directly into Web Settings.
     - If the preset API URL was modified, ensure its domain is also included in `UPSTREAM_HOST_ALLOWLIST`.

### Local Development

Create a project-local Python 3.11+ virtual environment first. The `.venv` directory is local developer state and is not provided by the repository.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend install
npm run backend:dev
```

In another terminal:

```bash
npm run frontend:dev
```

Open `http://localhost:5173`. Vite proxies `/api` and `/health` to FastAPI at `127.0.0.1:9090`.

Production-style local smoke test:

```bash
npm run frontend:build
ALLOW_UNAUTHENTICATED=true .venv/bin/granian --interface asgi backend.app.main:app --host 127.0.0.1 --port 9090
```

## Configuration

Most runtime options live in `.env.example`. API presets, prompt optimizer, R2 backup, and selected app/runtime options can also be managed through Web Settings / Overall Config. Important variables:

| Variable | Purpose |
| --- | --- |
| `ACCESS_KEY` | Access gate key. Required unless it is unset and `ALLOW_UNAUTHENTICATED=true`. |
| `ADMIN_KEY` | Settings management step-up key. Set a distinct value; if omitted it falls back to `ACCESS_KEY` and startup logs a warning. |
| `DEFAULT_API_URL` | Default upstream API base URL; may omit or include `/v1`. |
| `DEFAULT_API_KEY` | Default upstream API key. Prefer env refs such as `${OPENAI_API_KEY}` in Web Settings. |
| `DEFAULT_API_PATH` | `/v1/images/generations`, `/v1/responses`, or `/v1/chat/completions`. |
| `DEFAULT_RESPONSES_MODEL` | Fallback model for `/v1/responses` when no request/preset model is provided. |
| `AIOHTTP_CONNECTION_LIMIT` / `AIOHTTP_CONNECTION_LIMIT_PER_HOST` | Shared aiohttp connector limits for upstream/probe/download calls. |
| `APP_VERSION` / `GITHUB_REPO` / `ENABLE_VERSION_CHECK` | UI/API version reporting and latest-release checks. |
| `VERSION_CHECK_CACHE_SECONDS` | Per-process cache TTL for successful latest-release checks. |
| `MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB` / `UPSTREAM_MEMORY_BUDGET_MB` | Per-task decoded-image cap and process-local weighted upstream-memory admission budget. |
| `DB_EXECUTOR_WORKERS` / `SQLITE_BUSY_*` | Dedicated SQLite executor size and short-timeout jittered retry controls. |
| `IMAGE_CPU_CONCURRENCY` / `FILE_IO_CONCURRENCY` | Bounded full-image decode and blocking file-I/O concurrency per process. |
| `IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS` | Minimum interval for coalesced image-unit progress writes. |
| `RUNTIME_METRICS_REFRESH_SECONDS` / `EVENT_LOOP_LAG_SAMPLE_SECONDS` | Background coordination snapshot and event-loop lag sampling intervals. |
| `MAX_ACTIVE_GENERATE_JOBS` | Global running generation/edit image-unit limit. |
| `MAX_QUEUED_GENERATE_JOBS` | Queue capacity before new jobs return `429`. |
| `MAX_PENDING_EDIT_SOURCE_MB` | Global pending edit-source byte reservation cap. |
| `MAX_SSE_SUBSCRIBERS_GLOBAL` / `MAX_SSE_SUBSCRIBERS_PER_IP` / `SSE_CONNECTION_TTL_SECONDS` | SSE slot limits and max connection lifetime. |
| `IMAGES_DIR` | Saved image directory. |
| `THUMBNAILS_DIR` / `THUMBNAIL_*` | Gallery thumbnail storage and generation controls. |
| `DATA_DIR` / `DATABASE_FILE` | SQLite runtime storage. |
| `PROMPT_OPTIMIZER_*` | Optional server-side prompt optimizer settings. |
| `AI_ASSISTANT_*` | AI Assistant is enabled by default; set `AI_ASSISTANT_ENABLED=false` to disable it. API URL, key, text model, timeout, route, and host allowlist reuse `PROMPT_OPTIMIZER_*`. `AI_ASSISTANT_MAX_CONCURRENCY` caps concurrent upstream assistant calls and `AI_ASSISTANT_BATCH_MAX_IMAGES` caps one gallery AI batch. |
| `R2_*` | Optional Cloudflare R2 gallery backup sync settings; custom endpoint hosts require `R2_ENDPOINT_HOST_ALLOWLIST`. |
| `NODEIMAGE_API_KEY` | Optional NodeImage API key for server-side Gallery uploads. |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | Reverse-proxy Host/CSRF hardening. |
| `ENABLE_NGINX_ACCEL_REDIRECT` / `PUBLIC_IMAGE_BASE_URL` / `PUBLIC_THUMBNAIL_BASE_URL` | Optional nginx/CDN image byte serving behavior. |
| `GRANIAN_*` | Production runtime process/thread/static-asset tuning. |
| `ENABLE_METRICS` | Enables JSON/Prometheus metrics endpoints. |
| `LOG_DIR` / `LOG_LEVEL` / `LOG_RETENTION_HOURS` | Backend logs on stdout plus rotated files, retained for 24h by default. |

Secret fields prefer `${ENV_VAR_NAME}` references. Literal secrets stored in SQLite require `ALLOW_PLAINTEXT_SECRETS=true`.

Overall Config persists overrides in SQLite. Some settings are hot-reloaded; restart-required and build-only settings are marked in the UI and should still be changed through `.env`/Compose for reproducible deployments.

## Usage

1. Open the panel.
2. Unlock with `ACCESS_KEY` if enabled.
3. Open Settings.
4. Create or select an API preset.
5. Set API base URL, API path, model, response format, and API key/env ref.
6. Optionally configure SOCKS5 proxy, webhook, prompt optimizer, AI Assistant, R2 backup, NodeImage upload, or Overall Config overrides.
7. Save the preset and run its health check if needed.
8. Generate images from a prompt, or upload/select source images and run edits.
9. Use Gallery for reuse, filtering, favorites, batch actions, import/export, and R2 sync.

## Supported Upstream Paths

| Path | Notes |
| --- | --- |
| `/v1/images/generations` | Standard image generation endpoint; reads image data from `data[]`. |
| `/v1/responses` | Sends `prompt` and `model`; reads base64 image data from `image_generation_call` output items. |
| `/v1/chat/completions` | Sends OpenAI-compatible chat completions requests; extracts image URLs/base64 data from messages or SSE chunks. |
| `/v1/images/edits` | Used by the Edits flow; sends multipart source images and supported edit params. |

For `/v1/responses` and `/v1/chat/completions`, size/quality/format/compression/quantity controls are disabled because those paths do not share the same parameter contract.

## API Overview

Key backend routes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Health check. |
| `GET` | `/api/access/status` | Read access state. |
| `POST` | `/api/access` | Unlock the panel with the access key. |
| `GET/POST/DELETE` | `/api/access/admin`, `/api/access/admin/status` | Read, unlock, or clear the admin access state used by protected settings. |
| `GET` | `/api/version`, `/api/version/latest` | Read current version and optional latest release information. |
| `GET/PUT` | `/api/settings/overall-config` | Read/save Overall Config overrides. |
| `GET/POST` | `/api/settings` | Read/save active preset, prompt optimizer, R2 backup, proxy, and webhook settings. |
| `POST` | `/api/settings/presets` | Create an API preset. |
| `POST` | `/api/settings/presets/{preset_id}/activate` | Activate a saved API preset. |
| `DELETE` | `/api/settings/presets/{preset_id}` | Delete an API preset. |
| `POST` | `/api/settings/presets/{preset_id}/health` | Validate a saved upstream preset. |
| `POST` | `/api/settings/r2/health` | Validate draft R2 backup settings. |
| `GET/POST` | `/api/prompt-snippets` | List/create reusable prompt snippets. |
| `POST` | `/api/prompt-snippets/search` | Search reusable prompt snippets. |
| `PATCH/DELETE` | `/api/prompt-snippets/{snippet_id}` | Update/delete a prompt snippet. |
| `GET/POST` | `/api/prompt/optimizer-system-prompt` | Read/save the prompt optimizer system prompt. |
| `POST` | `/api/prompt/optimize`, `/api/prompt/optimizer-health` | Optimize a prompt or probe optimizer connectivity. |
| `POST` | `/api/assistant/health` | Probe AI Assistant connectivity. |
| `POST` | `/api/assistant/prompt/rewrite`, `/api/assistant/prompt/check`, `/api/assistant/prompt/variants` | Prompt Copilot rewrite, review, and variant tools. |
| `POST` | `/api/assistant/generate/recommend-params` | Recommend only generation parameters supported by the selected API path. |
| `POST` | `/api/assistant/jobs/{job_id}/diagnose`, `/api/assistant/edit/plan` | Diagnose a job or plan an edit without submitting it. |
| `POST` | `/api/assistant/image/prompt` | Reverse-prompt one validated local raster image in memory; returns a generation prompt without creating a Gallery record. |
| `POST` | `/api/assistant/image/prompt/optimize` | Optimize a reverse-prompt result together with its uploaded source image. |
| `POST/GET` | `/api/assistant/gallery/*` | Describe, reverse-prompt, analyze, batch-analyze, and read AI metadata for local gallery images. |
| `POST` | `/api/generate` | Start generation job. |
| `POST` | `/api/edits` | Start edit job with uploaded source images. |
| `POST` | `/api/edits/from-gallery/{image_id}` | Start edit job from an existing gallery image. |
| `GET` | `/api/generate/jobs` | List live jobs and optional persisted history. |
| `GET` | `/api/generate/jobs/events` | SSE stream for job-list updates. |
| `GET/DELETE` | `/api/generate/{job_id}` | Read or cancel one generation/edit job. |
| `GET` | `/api/generate/{job_id}/events` | SSE stream for one job. |
| `DELETE` | `/api/generate/jobs/history` | Clear terminal job history. |
| `GET` | `/api/gallery` | List/search/filter gallery images. |
| `POST` | `/api/gallery/search` | Search/filter gallery images with a JSON request body. |
| `GET/DELETE` | `/api/gallery/{image_id}` | Read or delete a gallery image. |
| `PATCH` | `/api/gallery/{image_id}/favorite` | Favorite/unfavorite one gallery image. |
| `POST/PATCH` | `/api/gallery/batch/*` | Selection-token, favorite, delete, and download batch actions. |
| `POST` | `/api/gallery/export-jobs`, `/api/gallery/direct-export-jobs` | Create async gallery export jobs. |
| `GET` | `/api/gallery/export-jobs/{job_id}`, `/api/gallery/direct-export-jobs/{job_id}` | Read async gallery export job status. |
| `GET` | `/api/gallery/export-jobs/{job_id}/events`, `/api/gallery/direct-export-jobs/{job_id}/events` | SSE streams for gallery export jobs. |
| `GET` | `/api/gallery/export-jobs/{job_id}/download` | Download a completed tracked export archive. |
| `POST` | `/api/gallery/sync-jobs` | Create an R2 backup sync job. |
| `GET` | `/api/gallery/sync-jobs/{job_id}`, `/api/gallery/sync-jobs/{job_id}/events` | Read or stream R2 backup sync job status. |
| `GET` | `/api/gallery/import-jobs/{job_id}` | Read async import job status. |
| `GET` | `/api/gallery/import-jobs/{job_id}/events` | SSE stream for async import job status. |
| `GET` | `/api/image/{filename}` | Serve authorized image bytes. |
| `GET` | `/api/thumb/{filename}` | Serve generated gallery thumbnail. |
| `GET` | `/api/download/{filename}` | Download one gallery image. |
| `GET` | `/api/download-all` | Stream gallery ZIP export. |
| `POST` | `/api/import` | Import gallery ZIP archive; `async_job=true` creates an import job. |
| `GET` | `/api/metrics`, `/api/metrics/prometheus` | Optional metrics when `ENABLE_METRICS=true`. |

The public API surface is contract-tested; keep paths, methods, status codes, SSE event names, cookies, and response shapes stable unless a breaking change is intentional.

## Contributor Boundaries

- Keep browser calls same-origin through `/api/*`; do not add direct frontend calls to upstream model APIs, R2, webhook targets, or arbitrary image URLs.
- Keep ownership boundaries intact:
  - routers/request orchestration in `backend/app/api/routers/`
  - DTOs in `backend/app/schemas/`
  - persistence and SQLite coordination in `backend/app/repositories/`
  - upstream integrations in `backend/app/integrations/`
  - mirrored frontend API types in `frontend/src/lib/api/types.ts`
- Keep public contracts stable unless a breaking change is intentional:
  - API paths, methods, status codes, cookies, SSE event names, and response shapes
  - generation/edit queue lifecycle, cancellation semantics, and multi-worker SQLite coordination
  - file-system races are tolerated by UUID filenames, atomic replacement, and orphan GC; do not rely on process-local locks for cross-worker exclusion
- Keep validation and safety centralized:
  - image byte validation, safe paths, thumbnail/archive helpers
  - SSRF-sensitive URL handling in validators, safe connector, and integration clients
  - secrets exposed to the frontend only as masked values or env-ref metadata
- Preserve current runtime constraints:
  - edits accept up to 16 raster source images
  - gallery ZIP import/export keeps existing safety limits
  - SSE uses SQLite slot leases with global/per-IP caps and TTL
  - R2 sync is backup-only; local SQLite rows and local image files remain the source of truth
- When changing environment variables, update `backend/app/core/settings.py`, `backend/app/core/overall_config.py` when user-visible, `.env.example`, `docker-compose.yml` when configurable in Compose, and this README.
- Do not commit runtime/generated artifacts such as `images/`, `data/`, `frontend/build/`, `.svelte-kit/`, Playwright reports, test results, dependency folders, local DB files, or logs.

## Testing

Activate the project-local `.venv` before running backend or contract tests. The npm contract/performance scripts use `.venv/bin/python`.

```bash
npm run frontend:check
npm run frontend:build
.venv/bin/python -m pytest backend/tests -q
npm run test:contract
npm run test:e2e
npm run test:perf
npm run test:e2e:perf
```

Run the focused subset relevant to your change. For release-bound or broad changes, run all of them.

If Playwright browsers are missing:

```bash
npm --prefix frontend exec playwright install chromium
```

## Contributing

For implementation boundaries and contributor-facing invariants, follow the `Contributor Boundaries` section above. Run the smallest relevant validation set for your change, and keep README/config updates in the same patch when behavior or environment variables change.

## License

This project is licensed under `CC BY-NC 4.0` (`Creative Commons Attribution-NonCommercial 4.0 International`).

See [LICENSE](./LICENSE).
