# GPT Image Panel

![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688?logo=fastapi)
![SvelteKit](https://img.shields.io/badge/SvelteKit-2-FF3E00?logo=svelte)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite)
![Docker](https://img.shields.io/badge/Docker-24.0+-2496ED?logo=docker)

Web panel for GPT Image 2 API image generation and editing.

English | [中文](#中文文档)

## Overview

GPT Image Panel is a lightweight FastAPI web UI for image generation and image editing. It is designed as a self-hosted panel that connects to an external GPT-compatible image API and stores generated images locally.

## Disclaimer

This project is only a self-hosted web visualization panel for initiating image generation and image editing requests. It does not provide, wrap, proxy, resell, or modify any upstream model or API service. All generation capability, content policy enforcement, account permissions, billing, and model behavior come from the upstream API provider configured by the user.

This project does not encourage or endorse generating pornographic, political, illegal, infringing, violent, hateful, or otherwise sensitive or policy-violating content. Users are solely responsible for their configured upstream service, prompts, uploaded images, generated results, storage, sharing, and any legal or platform-policy consequences. Any content generated through an upstream API is unrelated to this project and is not produced, reviewed, hosted, or approved by the project maintainers.

Key characteristics:

- SvelteKit + TypeScript frontend in `frontend/`
- FastAPI backend in `backend/app/`; use `backend.app.main:app` as the ASGI entrypoint
- public API paths, methods, status codes, SSE event names, cookies, and response shapes are contract-tested and kept stable
- API presets persisted to SQLite at `data/app.sqlite3`
- background generation/edit jobs executed with `asyncio.create_task`
- local image storage under `images/`
- gallery metadata stored in SQLite at `data/app.sqlite3`
- Docker and Docker Compose deployment support with frontend static build baked into the image
- pytest API contract tests under `backend/tests/`

## Features

- API preset management: base URL/path/key, per-preset default model and response format, global SOCKS5 upstream proxy, and global Webhook URL
- prompt helper tags, server-side prompt optimization, independent prompt snippets, and gallery prompt/parameter reuse
- generation and image-editing (`/v1/images/edits`) with size/quality/format/compression/quantity controls and up to 16 edit reference images
- preview + job history with SSE progress, multi-image result previews, `completed_at`, elapsed time, per-job stage timings, loading states, detailed terminal statuses, an errors-only history filter, clear-all for persisted history, cancel for queued/running jobs, and reuse/retry from persisted history
- shared queue and concurrency limits for generation/edit jobs
- optional global Webhook URL with HTTPS-only validation, SSRF checks, signing, retry, and masked settings responses
- gallery with filters (FTS-backed prompt search, model, preset, size, date range, favorite), URL-synced page/non-prompt-filter/lightbox/job-history state, direct page-number jump, lightbox previous/next navigation and left/right keyboard shortcuts, “Edit this image”, download, custom delete confirmations with 5-second undo for single images, batch actions with partial-success feedback, delete/delete-all, prompt/image-url copy, and on-demand total-size metadata
- prompt snippets drawer for reusable prompt templates, stored separately from gallery images in SQLite
- ZIP export/import (`metadata.json`) with streaming upload, safety validation, low-memory export path, skipped-entry metadata for partial batch downloads, and visible import/export/download progress states
- Cloudflare R2 gallery backup sync: configurable in `.env` or Web Settings, health-probed from Settings, and manually or periodically synced from Gallery without changing local gallery storage as the source of truth
- access-key gate, Host/public-origin allowlist, IP allowlist/proxy-header support, GitHub version badge, and CSP nonce injection
- observability hooks for job stage timings, slow `/api/gallery` query logging, queue/failure metrics, and optional JSON/Prometheus metrics endpoints

## Architecture

### Backend

The backend is a FastAPI application under `backend/app/`.

Responsibilities are split into a few modules:

- `backend/app/main.py` — thin ASGI entrypoint
- `backend/app/api/contract_app.py` — frozen public API surface and current route wiring
- `backend/app/schemas/` — Pydantic request/response DTOs
- `backend/app/repositories/` — SQLite, gallery, image file, settings, and job persistence
- `backend/app/integrations/` — upstream GPT-compatible image API client
- `backend/app/core/` — settings, access tokens, IP allowlist, proxy headers, and URL validators
- `backend/app/services/` — webhook signing, retry, and async delivery

When serving the static frontend, the backend injects a per-response script nonce into `frontend/build/index.html` and sends a matching Content Security Policy. Asset and index serving are covered by the backend contract tests.

### Frontend

The frontend is a SvelteKit static application in `frontend/`.
The backend serves only `frontend/build`; run a frontend build before starting the production backend.

It uses:

- Tailwind CSS
- `src/lib/api/client.ts` for same-origin fetch calls to existing `/api/*` endpoints
- `src/lib/api/events.ts` for SSE wrappers
- stores split across access, settings, prompt snippets, gallery, jobs, preview, and UI state
- components for access, header, settings drawer, prompt snippets drawer, prompt helper, job history drawer, preview, gallery, lightbox, and size selection

Frontend build:

```bash
npm --prefix frontend install
npm --prefix frontend run build
```

### Storage

Runtime persistent storage is minimal:

- generated images are saved in the `images/` directory
- gallery metadata, image byte sizes, FTS prompt-search index, and API presets are stored in SQLite at `data/app.sqlite3`, including `completed_at`, Beijing completion time, and generation duration
- prompt snippets are stored in SQLite at `data/app.sqlite3` independently from gallery metadata
- optional R2 backups are incremental object uploads only; local `images/` files and SQLite gallery rows remain the only gallery source used for serving, thumbnails, ZIP export, and import
- generation/edit job status, errors, timing, `completed_at`, and result metadata are stored in SQLite at `data/app.sqlite3`; successful multi-image jobs persist the full `images` result list while keeping the first result in `image_id`/`image_url` for compatibility
- image unit scheduling, leases, and parent job aggregation are stored in SQLite, so multiple Granian workers can share generation/edit work without Redis

### Generation flow

1. frontend calls `/api/generate`
2. backend validates config, creates one SQLite-backed parent job plus one image unit per requested output
3. SQLite queue/concurrency limits are enforced globally across Granian workers; progress stages stream via SSE
4. generation requests with `n > 1` stay as one public job, but count as `n` queue units and fan out through SQLite image-unit leases; `/v1/images/generations` unit payloads always use `n=1`
5. upstream image data is decoded/downloaded, validated, and saved; gallery metadata is updated
6. job history is queryable/streamed (`/api/generate/jobs*`), clearable (`DELETE /api/generate/jobs/history`), cancellable (`DELETE /api/generate/{job_id}`), and can trigger optional signed webhook callbacks

### Edit flow

1. frontend selects source images (one or more uploads, optionally combined with one gallery image) and calls `/api/edits` or `/api/edits/from-gallery/{image_id}`
2. backend creates a job and calls upstream `/v1/images/edits` using multipart form data
3. source images plus supported edit params are forwarded; multiple references are sent upstream as repeated `image[]` fields, and unsupported source formats (for example SVG) are rejected
4. progress stages stream via SSE; returned image data is decoded/downloaded, validated, and saved
5. edited results appear in preview/gallery and follow the same queue, history, and cancellation model as generation

## Tech stack

- Python 3.11+
- FastAPI
- Granian
- aiohttp
- SQLite
- Pydantic v2
- SvelteKit
- TypeScript
- Tailwind CSS

## Project structure

```text
LICENSE
README.md
Dockerfile
docker-compose.yml
.env.example
VERSION
requirements.txt
package.json
backend/
  requirements-dev.txt
  app/
    main.py
    api/
    core/
    schemas/
    repositories/
    integrations/
    services/
  tests/
frontend/
  package.json
  svelte.config.js
  vite.config.ts
  src/
    routes/
    lib/
      api/
      stores/
      components/
      utils/
deploy/
  nginx.conf
images/
data/
```

## Getting started

### Prerequisites

You need one of the following:

- Python 3.11 or newer
- Docker
- Docker Compose

An external GPT-compatible image API is required for actual image generation.

### Docker

Direct Docker runs expose the FastAPI/Granian process without nginx. This path keeps image responses on normal `FileResponse` and is useful for simple local deployments.

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 127.0.0.1:9090:9090 \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

If Docker Hub times out while resolving `python:3.11-slim`, use a reachable mirror image:

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

### Docker Compose

Compose runs nginx in front of the backend. The browser still uses `http://127.0.0.1:9090`; nginx owns that host port, proxies API/index requests to FastAPI, serves immutable SvelteKit assets directly, and sends authorized gallery image/thumbnail/download bytes through nginx internal redirects after the backend has checked access and gallery references.

```bash
cp .env.example .env
# edit .env if needed
# ACCESS_KEY is required by default unless ALLOW_UNAUTHENTICATED=true
docker-compose up -d --build --force-recreate
```

For Docker Hub timeout issues with Compose, set this in `.env` before building:

```bash
PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim
NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine
```

`GRANIAN_RUNTIME_THREADS` defaults to `2`. On machines with more available CPU, `GRANIAN_RUNTIME_THREADS=4` is a reasonable first bump. `GRANIAN_WORKERS>1` is supported for generation/edit fan-out plus tracked Gallery export/R2 sync jobs: workers claim SQLite-backed work, SSE polls SQLite state, and cancellation marks pending/running generation units without forcibly killing another process.

### Local development

```bash
pip install -r backend/requirements-dev.txt
npm --prefix frontend install
npm run backend:dev
```

In another terminal:

```bash
npm run frontend:dev
```

Then open `http://localhost:5173`. The Vite dev server proxies `/api` and `/health` to FastAPI at `127.0.0.1:9090`, so browser requests stay same-origin in development.
It binds to `127.0.0.1` by default; for LAN/external debugging, run `npm run frontend:dev -- --host 0.0.0.0`.

For a single-process local smoke test, build the frontend first and run FastAPI through Granian:

```bash
npm run frontend:build
granian --interface asgi backend.app.main:app --host 0.0.0.0 --port 9090 --reload
```

Then open `http://localhost:9090`.

If you want to run without access auth during local dev, set `ALLOW_UNAUTHENTICATED=true`. Startup logs a warning in that mode because every non-health API route is unauthenticated when `ACCESS_KEY` is unset.

### Health check

```bash
curl http://localhost:9090/health
```

## Usage

1. open the site
2. optionally use the top-left language switch to toggle English / Simplified Chinese
3. click the settings gear icon
4. choose an existing preset or click New
5. enter the API base URL
6. choose the API path
7. enter the preset default model; the Generate/Edit form's Model field defaults to the active preset's value
8. choose the preset default Response Format; the Generate/Edit form's Response format field defaults to the active preset's value
9. enter the API key, or an env ref such as `${OPENAI_API_KEY}`; literal keys require `ALLOW_PLAINTEXT_SECRETS=true`
10. optionally enter a global SOCKS5 proxy or env ref such as `${UPSTREAM_PROXY_URL}`
11. optionally enter a global Webhook URL or env ref such as `${WEBHOOK_URL}` for completed generation/edit jobs
12. optionally configure R2 Backup with endpoint URL, bucket, prefix, and access key env refs such as `${R2_ACCESS_KEY_ID}` / `${R2_SECRET_ACCESS_KEY}`, then click Test R2
13. optionally configure Prompt Optimizer with an endpoint URL, model, timeout seconds, and API key/env ref; literal keys require `ALLOW_PLAINTEXT_SECRETS=true`
14. optionally click Edit System Prompt in the Prompt Optimizer settings to edit the optimizer system prompt stored at `DATA_DIR/prompt_optimizer_system_prompt.md`
15. optionally open Overall Config for `.env.example` variables that are not already exposed in the main Settings form
16. optionally run Health check for the saved preset
17. click Save Preset
18. enter a prompt
19. click Prompt Helper tags to append common modifiers
20. click Optimize to rewrite the prompt through the server-side optimizer
21. open Prompts in the header to save or reuse prompt snippets; using a snippet replaces the current prompt
22. choose generation options, including API path for per-request upstream routing
23. click Generate
24. optionally upload one or more edit reference images, pick "Edit this image" in Gallery/Lightbox, or combine both; uploads append to the current edit sources and Clear removes all edit sources
25. click Edits to run image-to-image
26. use Gallery/Lightbox "Use prompt" or "Use all" to reuse historical prompt text or full parameters
27. view preview and gallery; click Gallery Sync to upload local gallery images missing from the configured R2 bucket prefix

## API paths

The panel supports these upstream paths. The API base URL may either omit or include `/v1`; for example, both `https://api.example.com` and `https://api.example.com/v1` are accepted.

### `/v1/images/generations`

- sends generation requests to the Images API
- reads image data from `data[]`

### `/v1/responses`

- sends generation requests to the Responses API
- sends only `prompt` and `model` in the upstream request body; the UI model default comes from the active preset
- reads base64 image data from `output[]` items of type `image_generation_call`
- size, quality, format, compression, quantity, and response format controls are disabled in the UI for this path

### `/v1/chat/completions`

- sends OpenAI Chat Completions-compatible generation requests
- sends only `model`, `messages`, and `stream: false` in the upstream request body
- supports `grok-imagine-image-lite` and other image models that return image URLs or base64 data through chat completion messages
- reads image output from JSON chat completions or `data:` SSE chunks, including Markdown image links such as `![image](https://...)`
- size, quality, format, compression, quantity, and response format controls are disabled in the UI for this path

### `/v1/images/edits`

- used by the Edits button after image upload(s), gallery-image selection, or both
- always calls `/v1/images/edits` on the configured API base URL
- sends multipart/form-data with source image fields plus supported edit parameters; single uploads use `image`, while multiple references are forwarded upstream as repeated `image[]`
- supports up to 16 edit reference images total; local uploads append to the current source list and can be combined with one gallery source
- uploaded source files must be supported raster image formats; SVG uploads are rejected
- if the upstream returns `404`, `405`, or `501`, the UI reports that `/v1/images/edits` is not supported and stops the edit request

## API preset health checks

- `POST /api/settings/presets/{preset_id}/health` validates the saved preset without sending a generation request
- checks include API path allowability, HTTPS URL/hostname validation, upstream host allowlist and SSRF DNS/private-IP validation, API key/env-ref presence, and a low-cost `OPTIONS`/`HEAD` upstream probe
- upstream probe failures are diagnostic only: `OPTIONS 404/410` is reported as a warning because many GPT-compatible gateways only implement `POST`, and an isolated `upstream_probe` error does not fail the overall preset health
- returned shape is `{ status, checks: [{ name, status, message }] }`, where each status is `ok`, `warning`, or `error`
- API key env refs use the exact `${ENV_VAR_NAME}` form; the database stores the reference string and generation/edit calls resolve it from the server environment at request time
- literal API keys are rejected by default; set `ALLOW_PLAINTEXT_SECRETS=true` only if you intentionally want plaintext SQLite storage

## Overall Config

- Settings includes an `Overall Config` modal for `.env.example` variables that are not already managed by the main Settings form.
- On startup, the server syncs current process environment values into SQLite. Effective priority is `SQLite override > current env > code default`.
- Saving in the modal writes SQLite overrides; it does not rewrite `.env`. Container rebuilds keep saved overrides as long as `data/app.sqlite3` is preserved.
- Secret values are stored as real override values when saved, but API responses and the UI only return masked values. Sending `********` preserves the existing secret override; `Reset to .env` clears that override.
- Hot-reloadable fields update in memory immediately. Runtime paths, access/session startup behavior, queue concurrency, and Docker build image values are marked as restart-required or build-only.
- `DATABASE_FILE`, `DATA_DIR`, `IMAGES_DIR`, and `THUMBNAILS_DIR` can be displayed and saved as overrides, but the running process cannot move the current SQLite connection or storage directories without restart.

## Upstream SOCKS5 proxy

- The Settings drawer has one global `SOCKS5 proxy` field, independent of API presets.
- Leave it empty for direct upstream API calls.
- Use an env ref such as `${UPSTREAM_PROXY_URL}` by default. Literal values like `socks5://host:port` or `socks5://user:pass@host:port` require `ALLOW_PLAINTEXT_SECRETS=true`; stored proxy passwords are masked in API responses and the UI.
- The proxy boundary is intentionally narrow: only generation/edit upstream API `POST` calls use it. Preset health checks, webhooks, version checks, frontend `/api/*` requests, and image URL downloads stay direct.

## Global Webhook URL

- The Settings drawer has one global `Webhook URL` field directly below `SOCKS5 proxy`; it is independent of API presets.
- Leave it empty to disable job callbacks.
- Use an env ref such as `${WEBHOOK_URL}` by default. Literal webhook URLs require `ALLOW_PLAINTEXT_SECRETS=true`.
- When configured, completed generation/edit jobs send signed webhook callbacks to that HTTPS URL.
- `WEBHOOK_SIGNING_SECRET` is required when a Webhook URL is configured. Stored webhook URLs are masked in API responses and the UI.

## R2 gallery backup sync

- R2 Backup is an incremental backup path, not remote gallery storage.
- Configure it in `.env` with `R2_BACKUP_ENABLED=true` plus the other `R2_*` variables, or in Web Settings. When SQLite has no saved R2 settings yet, startup persists the current `.env` R2 values so they appear in Web Settings. Later Web Settings saves take precedence. Web Settings accepts `${ENV_VAR_NAME}` refs for credentials; literal stored credentials require `ALLOW_PLAINTEXT_SECRETS=true`.
- Test R2 in Settings runs `HeadBucket`, a prefix-scoped `ListObjectsV2`, and a small probe object write; probe cleanup failure is reported as a warning.
- The Gallery Sync button uploads only local gallery filenames missing from `R2_KEY_PREFIX`; existing bucket objects are skipped, and bucket-only objects are never deleted or overwritten.
- Set `R2_SYNC_INTERVAL_HOURS` or the Web Settings interval to a positive integer to run the same incremental sync periodically. `0` disables automatic sync, and startup waits one full interval before the first scheduled run.

## Image size modes

- `auto` — default; let the model choose the output size
- ratio presets — 1K, 2K, or 4K with ratios `1:1`, `4:3`, `3:4`, `16:9`, `9:16`, or `21:9`
- custom width and height — values are normalized to multiples of 16, max side `3840px`, aspect ratio up to `3:1`, and total pixels between `655360` and `8294400`

## Generation options

- Quality: `auto`, `low`, `medium`, or `high`
- Format: `PNG`, `JPEG`, or `WebP`
- Compression: disabled for `PNG`; `0-100` for `JPEG` and `WebP`
- Quantity: integer from `1` to `10`; the field can be cleared while editing, and Generate/Edit will restore an empty value to `1` on submit
- Response Format: defaults to the active preset's value (`url` by default), with `none` and `b64_json` still available; `none` omits the `response_format` parameter

## Import and upload limits

- each uploaded source image is limited by `MAX_FILE_SIZE_MB`, must pass full Pillow decode validation plus pixel-bomb limits, and must be a supported raster format (`.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.avif`, `.bmp`, `.heic`, `.heif`, `.ico`, `.tif`, `.tiff`); SVG is rejected
- `/api/import` accepts ZIP archives created by `/api/download-all`
- import archives must include `metadata.json`
- selected-image ZIP downloads include `metadata.skipped` when requested gallery rows or image files are missing
- import archives are validated for uploaded size, file count, total uncompressed size, metadata size, member-path safety, and compression ratio
- imported image entries must pass file extension, content-type/magic-byte, full decoder, and pixel-count validation before they are stored

## Environment variables

All variables listed in `.env.example` are also tracked in SQLite for Overall Config. Variables already exposed in the main Settings form stay there; the Overall Config modal shows the remaining runtime/security/limit/build knobs.

| Variable | Default | Description |
|----------|---------|-------------|
| `DEFAULT_API_URL` | empty | Pre-fill API base URL; may omit or include `/v1` |
| `DEFAULT_API_KEY` | empty | Pre-fill API key; use an env ref such as `${OPENAI_API_KEY}` by default. Literal keys require `ALLOW_PLAINTEXT_SECRETS=true` |
| `DEFAULT_API_PATH` | `/v1/images/generations` | Default upstream path; supported values are `/v1/images/generations`, `/v1/responses`, and `/v1/chat/completions` |
| `DEFAULT_RESPONSES_MODEL` | `gpt-5.4` | Fallback top-level model used for `/v1/responses` when no request/preset model is provided |
| `DEFAULT_UPSTREAM_SOCKS5_PROXY` | empty | Optional default global SOCKS5 proxy or env ref for generation/edit upstream API calls |
| `AIOHTTP_CONNECTION_LIMIT` | `100` | Total aiohttp connector connection limit for upstream/probe/download calls |
| `AIOHTTP_CONNECTION_LIMIT_PER_HOST` | `20` | Per-host aiohttp connector connection limit; `0` disables the per-host cap |
| `ALLOW_PLAINTEXT_SECRETS` | `false` | Allow literal API keys / SOCKS5 proxy URLs / Webhook URLs / R2 credentials to be persisted in SQLite instead of requiring `${ENV_VAR_NAME}` refs |
| `PROMPT_OPTIMIZER_ENABLED` | `false` | Enable the server-side prompt optimizer |
| `PROMPT_OPTIMIZER_API_URL` | empty | Full Chat Completions-compatible endpoint URL used by the prompt optimizer |
| `PROMPT_OPTIMIZER_API_KEY` | empty | Prompt optimizer API key; use an env ref such as `${OPENAI_API_KEY}` by default. Literal keys require `ALLOW_PLAINTEXT_SECRETS=true` |
| `PROMPT_OPTIMIZER_MODEL` | `gpt-4o-mini` | Prompt optimizer model |
| `PROMPT_OPTIMIZER_TIMEOUT_SECONDS` | `60` | Prompt optimizer request timeout in seconds |
| `PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS` | `4000` | Max optimized prompt length returned to the textarea |
| `PROMPT_OPTIMIZER_MAX_RESPONSE_MB` | `8` | Max optimizer upstream response body size in MB before JSON parsing |
| `PROMPT_OPTIMIZER_HOST_ALLOWLIST` | empty | Optional comma-separated hostname allowlist for the optimizer endpoint |
| `R2_BACKUP_ENABLED` | `false` | Enable Gallery Sync backup when using environment-based R2 configuration |
| `R2_ENDPOINT_URL` | empty | Cloudflare R2 S3-compatible endpoint URL, for example `https://ACCOUNT_ID.r2.cloudflarestorage.com` |
| `R2_BUCKET_NAME` | empty | Bucket used by Gallery Sync backup |
| `R2_REGION` | `auto` | S3 client region name for R2 |
| `R2_KEY_PREFIX` | `gallery/` | Object key prefix for gallery backups; use a dedicated prefix such as `gallery-test/` for validation |
| `R2_SYNC_INTERVAL_HOURS` | `0` | Scheduled Gallery Sync interval in hours; `0` disables automatic sync |
| `R2_ACCESS_KEY_ID` | empty | R2 access key ID used by env-ref credentials |
| `R2_SECRET_ACCESS_KEY` | empty | R2 secret access key used by env-ref credentials |
| `APP_VERSION` | `VERSION` file | Override the app version shown in the UI and returned by `/api/version`; read on each request |
| `GITHUB_REPO` | `Z1rconium/gpt-image-linux` | GitHub `owner/repo` used for latest-release update detection; set empty to disable latest-version checks |
| `ENABLE_VERSION_CHECK` | `true` | Enable per-request GitHub latest-version checks for the header `New` badge |
| `VERSION_CHECK_TIMEOUT_SECONDS` | `3` | Timeout for each GitHub latest release or branch `VERSION` request |
| `VERSION_CHECK_BRANCH` | `main` | Branch used for the fallback raw `VERSION` file check |
| `ENABLE_METRICS` | `false` | Enable `/api/metrics` JSON counters/gauges/rates/latency summaries and Prometheus text output |
| `SLOW_GALLERY_QUERY_MS` | `200` | Log `/api/gallery` requests at or above this threshold with prompt presence/length/hash, other filters, page, total, and DB query time |
| `ENABLE_NGINX_ACCEL_REDIRECT` | `false` | Return `X-Accel-Redirect` for authorized image/thumb/download responses when nginx internal aliases are in front; Compose enables this by default |
| `ACCESS_KEY` | empty | Required by default; all non-health routes require unlock when set |
| `ALLOW_UNAUTHENTICATED` | `false` | Set `true` to explicitly allow startup without `ACCESS_KEY`; startup logs a warning because non-health API routes are unauthenticated |
| `ACCESS_KEY_COOKIE_NAME` | `gpt_image_access` | Browser cookie name used for the access-key session |
| `ACCESS_COOKIE_SECURE` | `true` | Mark the access cookie Secure; set `false` only for plain-HTTP local/private deployments |
| `ACCESS_MAX_FAILURES` | `5` | Failed access-key attempts before temporary lockout |
| `ACCESS_LOCKOUT_SECONDS` | `300` | Lockout duration after too many failed access attempts |
| `IP_ALLOWLIST` | empty | Comma-separated allowed IPs/CIDRs |
| `TRUST_PROXY_HEADERS` | `false` | Read `X-Forwarded-For`, `X-Real-IP`, `X-Forwarded-Proto`, or `X-Forwarded-Host` from a trusted reverse proxy |
| `TRUSTED_PROXY_IPS` | empty | Optional comma-separated proxy IPs/CIDRs allowed to provide trusted proxy headers |
| `PUBLIC_ORIGIN` | empty | Optional canonical browser origin such as `https://panel.example.com`; also contributes to the Host allowlist and CSRF expected origin |
| `ALLOWED_HOSTS` | empty | Optional comma-separated allowed Host / trusted `X-Forwarded-Host` values; accepts hostnames, `host:port`, or origins |
| `CSRF_ORIGIN_CHECK_ENABLED` | `true` | Reject unsafe `POST`, `PATCH`, and `DELETE` requests unless `Origin`, `Referer`, or same-origin `Sec-Fetch-Site` proves the browser source |
| `UPSTREAM_HOST_ALLOWLIST` | empty | Optional comma-separated hostname allowlist for generation/edit upstream API URLs; with a SOCKS5 upstream proxy, the proxy is the trust boundary for remote DNS/network reachability |
| `WEBHOOK_HOST_ALLOWLIST` | empty | Optional comma-separated webhook hostname allowlist |
| `MAX_FILE_SIZE_MB` | `50` | Max uploaded image size in MB for edit source images, imported image files, and downloaded upstream image URLs |
| `MAX_JSON_BODY_MB` | `1` | Max JSON request body size in MB for non-upload API calls |
| `MAX_UPSTREAM_JSON_MB` | `128` | Max upstream JSON/SSE response body size in MB before parsing; prefer `response_format=url` for large or multi-image results |
| `MAX_IMAGE_PIXELS` | `100000000` | Max decoded image pixels accepted by Pillow before decompression-bomb rejection |
| `IMPORT_ARCHIVE_MAX_MB` | `1000` | Max uploaded ZIP size in MB for `/api/import` |
| `IMPORT_MAX_FILES` | `500` | Max number of files allowed inside one import archive |
| `IMPORT_MAX_UNCOMPRESSED_MB` | `1024` | Max total uncompressed size in MB across all files in an import archive |
| `IMPORT_MAX_METADATA_BYTES` | `2097152` | Max `metadata.json` size in bytes for an import archive |
| `IMPORT_MAX_COMPRESSION_RATIO` | `100` | Max allowed uncompressed/compressed ratio for any imported file |
| `GRANIAN_WORKERS` | `1` | Granian worker processes; values `>1` share generation/edit image units through SQLite |
| `MAX_ACTIVE_GENERATE_JOBS` | `2` | Max number of generation/edit image units running globally |
| `MAX_QUEUED_GENERATE_JOBS` | `20` | Max additional queued generation/edit image units before new requests are rejected with `429` |
| `IMAGE_JOB_UNIT_LEASE_SECONDS` | `120` | SQLite claim lease for a running image unit before another worker may retry it |
| `IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS` | `0.35` | SQLite polling interval for image-unit dispatch and generation SSE |
| `MAX_PENDING_EDIT_SOURCE_MB` | `200` | Max total pending edit source image bytes in MB; set `0` to disable this byte cap |
| `MAX_SSE_SUBSCRIBERS_GLOBAL` | `200` | Max active SSE subscribers across the process |
| `MAX_SSE_SUBSCRIBERS_PER_IP` | `10` | Max active SSE subscribers per client IP |
| `SSE_CONNECTION_TTL_SECONDS` | `3600` | Maximum lifetime for one SSE connection |
| `IMAGES_DIR` | `./images` | Directory for saved images |
| `THUMBNAILS_DIR` | `./images/thumbs` | Directory for generated gallery thumbnails |
| `THUMBNAIL_MAX_SIDE` | `512` | Max thumbnail width/height in pixels |
| `DATA_DIR` | `./data` | Directory for SQLite runtime data |
| `DATABASE_FILE` | `./data/app.sqlite3` | SQLite database for gallery metadata and API presets |
| `PYTHON_BASE_IMAGE` | `python:3.11-slim` | Docker build base image; override when Docker Hub is slow or blocked |
| `NODE_BASE_IMAGE` | `node:24-alpine` | Docker frontend build base image; override when Docker Hub is slow or blocked |
| `WEBHOOK_SIGNING_SECRET` | empty | Required when a global Webhook URL is configured; used to sign webhook payloads (`X-Webhook-Signature`) |
| `WEBHOOK_TIMEOUT_SECONDS` | `5` | Webhook delivery timeout per attempt (seconds) |
| `WEBHOOK_MAX_ATTEMPTS` | `3` | Max webhook delivery retry attempts |

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Frontend UI |
| `GET` | `/health` | Health check |
| `GET` | `/api/version` | Current app version, configured GitHub repo, and latest-release URL; requires access unlock when `ACCESS_KEY` is set |
| `GET` | `/api/access/status` | Check access-key session status |
| `POST` | `/api/access` | Unlock access for 3 hours |
| `POST` | `/api/settings` | Save the active API preset |
| `GET` | `/api/settings` | Get current settings and presets |
| `GET` | `/api/settings/overall-config` | List environment-backed Overall Config fields, masked secrets, sources, and restart/build-only metadata |
| `PUT` | `/api/settings/overall-config` | Save or clear SQLite overrides for Overall Config fields |
| `POST` | `/api/settings/r2/health` | Validate a draft R2 Backup config and run bucket/list/write checks |
| `POST` | `/api/prompt/optimize` | Rewrite a prompt through the server-side optimizer |
| `GET` | `/api/prompt/optimizer-system-prompt` | Read the Prompt Optimizer system prompt, falling back to the built-in default |
| `POST` | `/api/prompt/optimizer-system-prompt` | Save the Prompt Optimizer system prompt to `DATA_DIR/prompt_optimizer_system_prompt.md` |
| `GET` | `/api/prompt-snippets` | List prompt snippets, optionally filtered by `query` |
| `POST` | `/api/prompt-snippets` | Create a prompt snippet |
| `PATCH` | `/api/prompt-snippets/{snippet_id}` | Update a prompt snippet title, prompt, or favorite flag |
| `DELETE` | `/api/prompt-snippets/{snippet_id}` | Delete a prompt snippet |
| `POST` | `/api/settings/presets` | Create and activate an API preset |
| `POST` | `/api/settings/presets/{preset_id}/activate` | Activate an API preset |
| `POST` | `/api/settings/presets/{preset_id}/health` | Validate a saved API preset and run a low-cost upstream probe |
| `DELETE` | `/api/settings/presets/{preset_id}` | Delete an API preset |
| `POST` | `/api/generate` | Start an image generation job |
| `POST` | `/api/edits` | Start an image edit job with one or more multipart image uploads |
| `POST` | `/api/edits/from-gallery/{image_id}` | Start an image edit job using an existing gallery image, optionally with uploaded references |
| `GET` | `/api/generate/jobs` | List queued/running generation and edit jobs; pass `include_finished=true` with optional `limit`/`offset` for paginated persisted history, and `failed_only=true` to return only `error`/`upstream_error` history rows |
| `GET` | `/api/generate/jobs/events` | Stream queued/running generation and edit jobs over SSE |
| `DELETE` | `/api/generate/jobs/history` | Delete all persisted terminal generation/edit job history rows; queued/running jobs and gallery images are kept |
| `GET` | `/api/generate/{job_id}` | Get generation job status or result |
| `GET` | `/api/generate/{job_id}/events` | Stream generation job status/progress over SSE |
| `DELETE` | `/api/generate/{job_id}` | Cancel and remove a queued/running generation or edit job |
| `GET` | `/api/gallery` | List gallery images with pagination and optional `prompt`, `model`, `preset`, `size`, `date_from`, `date_to`, `favorite`, and `include_total_bytes` filters |
| `PATCH` | `/api/gallery/{id}/favorite` | Set or clear a gallery favorite flag |
| `GET` | `/api/gallery/{image_id}` | Get a single gallery entry by ID |
| `GET` | `/api/image/{filename}` | Serve image file |
| `GET` | `/api/thumb/{filename}` | Serve or lazily create a WebP gallery thumbnail |
| `GET` | `/api/download/{filename}` | Download image as attachment |
| `DELETE` | `/api/gallery/{id}` | Delete gallery entry and its server image file |
| `GET` | `/api/download-all` | Download all gallery images plus `metadata.json` as a ZIP file |
| `POST` | `/api/gallery/export-jobs` | Start a tracked gallery ZIP export job; optionally pass `ids` for selected images |
| `GET` | `/api/gallery/export-jobs/{job_id}` | Get tracked ZIP export job status |
| `GET` | `/api/gallery/export-jobs/{job_id}/events` | Stream ZIP export pack progress over SSE |
| `GET` | `/api/gallery/export-jobs/{job_id}/download` | Download a completed tracked ZIP export with `Content-Length` transfer progress |
| `POST` | `/api/gallery/sync-jobs` | Start a single active R2 gallery backup sync job |
| `GET` | `/api/gallery/sync-jobs/{job_id}` | Get R2 sync job status |
| `GET` | `/api/gallery/sync-jobs/{job_id}/events` | Stream R2 sync progress over SSE |
| `POST` | `/api/import` | Import a ZIP created by `/api/download-all` |
| `DELETE` | `/api/gallery` | Delete all gallery entries and server image files |
| `GET` | `/api/metrics` | Optional metrics snapshot; returns JSON by default or Prometheus exposition format with `Accept: text/plain`; only available when `ENABLE_METRICS=true` |
| `GET` | `/api/metrics/prometheus` | Optional Prometheus exposition metrics; only available when `ENABLE_METRICS=true` |

## Runtime behavior notes

- app version comes from `APP_VERSION` then `VERSION`; both the local app version and optional GitHub remote check are evaluated on each web-triggered version request after access unlock. The remote check reads the latest release first, falls back to the configured branch `VERSION`, and can show a `New` badge without blocking usage.
- when `PUBLIC_ORIGIN` or `ALLOWED_HOSTS` is configured, unknown `Host` or trusted `X-Forwarded-Host` values are rejected before CSRF checks; unsafe browser requests must include `Origin`, `Referer`, or same-origin `Sec-Fetch-Site`, and `Sec-Fetch-Site: same-origin` does not bypass the Host allowlist
- upstream-returned image URLs must use HTTPS; plain HTTP image downloads are rejected before the backend fetches them
- when an upstream SOCKS5 proxy is configured, the backend still validates the upstream URL before connecting and logs a warning if the hostname resolves locally to private/internal IPs, but the SOCKS5 proxy is the trust boundary for remote DNS and network reachability
- presets, prompt snippets, and gallery/job data persist only in `DATABASE_FILE`
- SQLite repository operations use short-lived connections with WAL enabled at startup; `DATA_DIR` is chmodded to `0700`, the SQLite DB/sidecars are chmodded to `0600`, and app shutdown/tests call the storage close hook so connection lifecycle stays explicit
- generation and edit share one SQLite queue measured in image units (`MAX_ACTIVE_GENERATE_JOBS` + `MAX_QUEUED_GENERATE_JOBS`), all edit source images are staged under `DATA_DIR/edit-sources` and additionally capped by `MAX_PENDING_EDIT_SOURCE_MB`, support cancellation, and persist terminal history including `completed_at`
- batch generation/edit (`n > 1`) consumes `n` queue units; the parent job aggregates successful unit results into `images[]`, Gallery metadata keeps the user-requested `n`, and units can be claimed by different Granian workers
- tracked Gallery export jobs and manual/scheduled R2 sync jobs persist in SQLite, so create/query/SSE/download work across `GRANIAN_WORKERS>1`; workers claim queued or expired running jobs with SQLite leases while export ZIP files live under shared `DATA_DIR/exports`
- Prompt Optimizer uses its own server-side Chat Completions-compatible endpoint config and user-configurable request timeout/response-size cap, resolves API key env refs on the backend, stores its editable system prompt in `DATA_DIR/prompt_optimizer_system_prompt.md`, and does not consume generation/edit queue capacity.
- R2 Backup uses boto3 against a Cloudflare R2 S3-compatible endpoint under `asyncio.to_thread`; sync jobs list only the configured prefix, upload missing local gallery filenames, and never serve, overwrite, or delete gallery images from R2.
- SSE is the primary progress channel; `/api/generate/jobs` provides list/history (`include_finished=true`, optional `limit`/`offset`, optional `failed_only=true`), `/api/generate/jobs/history` clears terminal history, and `/api/generate/jobs/events` streams SQLite-backed live job-list changes with sub-second polling
- terminal job history includes `stage_timings` for `upstream_wait`, `download_decode`, `validate`, `thumbnail`, and `db_insert`; slow gallery queries are logged with prompt presence/length/hash plus other filters and totals and counted in metrics; optional metrics include queue depth, running jobs, failure ratios, job-stage latencies, and slow SQLite query counters; terminal job statuses distinguish `cancelled`, `interrupted`, and `upstream_error` in addition to the generic `error`
- upstream JSON/SSE bodies are read with a `MAX_UPSTREAM_JSON_MB` cap before parsing, JSON request bodies are capped by `MAX_JSON_BODY_MB`, and upstream image URL downloads are revalidated (SSRF-aware, no blind redirect follow), fully decoded with Pillow, pixel-limited by `MAX_IMAGE_PIXELS`, and bounded by `MAX_FILE_SIZE_MB`
- `/api/import` enforces ZIP safety/size/count/compression checks; `/api/download-all` keeps the low-memory streaming path, while tracked export jobs write temp ZIP files so UI progress can cover both packing and transfer
- gallery stores byte-size metadata and thumbnails (`THUMBNAILS_DIR`), with lazy thumbnail and opt-in byte-size backfill for older images; with Compose, nginx serves immutable frontend assets directly and serves image/thumb/download file bytes only after FastAPI returns an authorized `X-Accel-Redirect`
- startup reconciliation removes gallery rows for missing files and marks previously running/queued jobs as interrupted

## Testing

```bash
npm run frontend:check
npm run frontend:build
python3 -m pytest backend/tests/test_contract.py -q
npm run test:e2e
RUN_PERFORMANCE_TESTS=true python3 -m pytest backend/tests/test_performance.py -q
npm run test:e2e:perf
```

The contract tests cover the frozen public API surface, including access cookies, settings, generation/edit job creation, job timing metrics, SSE response framing, gallery import/export, frontend index/CSP handling, static asset access, downloads, validation errors, and 500 error shape. Playwright covers access gate, settings drawer focus behavior, mocked generate/edit flows, gallery filtering/page jumps/batch actions, toast live regions, and lightbox keyboard close/navigation.

## Contributing

Contributions are welcome.

Helpful guidelines:

- keep backend changes simple and explicit
- update `VERSION` when a user-visible change or release-worthy fix warrants a new `vMAJOR.MINOR.PATCH` version
- use FastAPI response models from `backend/app/schemas/` where applicable
- keep persistent storage operations centralized in `backend/app/repositories/`
- keep upstream API interaction centralized in `backend/app/integrations/`
- keep browser requests on same-origin `/api/*` paths; do not introduce direct cross-origin frontend-to-backend calls
- avoid storing real API keys in repository files
- do not commit generated images or runtime gallery metadata unless explicitly requested
- preserve the existing async generation flow and SSE progress model unless the change explicitly requires altering job lifecycle behavior

## License

This project is licensed under `CC BY-NC 4.0` (`Creative Commons Attribution-NonCommercial 4.0 International`).

- You can use, copy, modify, redistribute, and create derivative works.
- You must provide attribution and keep the license notice.
- You may not use this project or derivative works for commercial purposes.
- If you need commercial use, you must obtain prior permission from the copyright holder.

See [LICENSE](./LICENSE) for the repository license text.

---

# 中文文档

# GPT Image Panel

GPT Image 2 API 图像生成和编辑 Web 面板。

[English](#gpt-image-panel) | 中文

## 概述

GPT Image Panel 是一个轻量级 FastAPI Web 界面，用于图像生成和图像编辑。它被设计为自托管面板，连接外部 GPT 兼容图像 API，并在本地存储生成的图片。

## 免责声明

本项目仅是一个用于发起图像生成和图像编辑请求的自托管 Web 可视化面板。本项目不提供、不封装、不代理、不转售、也不修改任何上游模型或 API 服务。实际生成能力、内容政策执行、账号权限、计费规则和模型行为均来自用户自行配置的上游 API 服务商。

本项目不鼓励、不支持生成色情、政治、违法、侵权、暴力、仇恨，或其他敏感及违反政策的内容。用户需要自行对其配置的上游服务、提示词、上传图片、生成结果、存储、传播，以及由此产生的法律或平台政策后果负责。任何通过上游 API 生成的内容均与本项目无关，亦不代表项目维护者生成、审核、托管或认可该内容。

主要特点：

- SvelteKit + TypeScript 前端位于 `frontend/`
- FastAPI 后端位于 `backend/app/`；ASGI 入口使用 `backend.app.main:app`
- 公共 API 路径、方法、状态码、SSE 事件名、cookie 和响应结构通过契约测试冻结
- API 预设持久化保存在 SQLite：`data/app.sqlite3`
- 生成/编辑任务通过 `asyncio.create_task` 异步执行
- 图片保存在 `images/`
- Gallery 元数据保存在 SQLite：`data/app.sqlite3`
- Docker 镜像会构建并内置 SvelteKit 静态前端
- pytest API 契约测试位于 `backend/tests/`

## 功能

- API 预设管理：base URL/path/key、每个预设的默认 model 和 Response Format、全局 SOCKS5 上游代理和全局 Webhook URL
- 提示词助手标签、服务端提示词优化器、独立提示词收藏夹，以及 Gallery 提示词/参数复用
- 图像生成 + 图生图编辑（`/v1/images/edits`），支持尺寸/质量/格式/压缩率/数量等参数，并支持最多 16 张编辑参考图
- 预览 + 历史任务：SSE 进度、多图结果预览、`completed_at`、耗时、任务分段耗时、加载状态、细分终态状态、仅错误历史筛选、清空持久化历史、排队/运行任务取消，以及从持久化历史复用/重试
- 生成与编辑共享并发和排队限制
- 可选全局 Webhook URL：HTTPS 校验、SSRF 防护、签名、重试，以及设置响应打码
- Gallery：筛选（FTS 提示词搜索、模型、预设、尺寸、日期区间、收藏）、URL 同步的 page/非提示词筛选/lightbox/job history 状态、页码输入跳转、Lightbox 上一张/下一张导航和左右方向键快捷键、”Edit this image”、下载/删除、批量操作部分成功反馈、单图 5 秒撤销删除、复制提示词/图片链接、按需总大小统计
- 提示词收藏夹：可复用 prompt 模板，与 Gallery 图片分开存储和管理
- ZIP 导出导入（含 `metadata.json`）+ 流式上传 + 安全校验 + 低内存导出路径 + 批量下载 skipped metadata + 可见导入/导出/下载进度状态
- Cloudflare R2 Gallery 备份同步：可通过 `.env` 或 Web Settings 配置，可在 Settings 测试连通性，并可从 Gallery 手动或定时增量同步；本地 Gallery 存储仍是唯一源数据
- 访问密钥、Host/public origin 白名单、IP 白名单/反向代理头、版本检测、CSP nonce
- 观测能力：任务分段耗时、慢 `/api/gallery` 查询日志、队列/失败率指标、可选 JSON/Prometheus metrics

## 架构

### 后端

后端是 FastAPI 应用，位于 `backend/app/`。

功能拆分为以下模块：

- `backend/app/main.py` — 很薄的 ASGI 入口
- `backend/app/api/contract_app.py` — 冻结的公共 API 表面和当前路由组装
- `backend/app/schemas/` — Pydantic 请求/响应 DTO
- `backend/app/repositories/` — SQLite、Gallery、图片文件、settings 和 jobs 持久化
- `backend/app/integrations/` — 上游 GPT 兼容图片 API 调用
- `backend/app/core/` — settings、访问 token、IP allowlist、proxy header 和 URL 校验
- `backend/app/services/` — webhook 签名、重试和异步投递

后端服务静态前端时，会为 `frontend/build/index.html` 注入每次响应不同的 script nonce，并发送匹配的 Content Security Policy。前端入口和静态资源访问已纳入后端契约测试。

### 前端

前端是 SvelteKit 静态应用，位于 `frontend/`。
后端只服务 `frontend/build`；生产方式启动后端前需要先完成前端构建。

使用技术：

- Tailwind CSS
- `src/lib/api/client.ts` 封装同源 `/api/*` fetch
- `src/lib/api/events.ts` 封装 SSE
- stores 拆分为 access、settings、prompt snippets、gallery、gallery actions、edit source、jobs、preview、lightbox、version 和 UI
- 组件拆分为 access、header、settings drawer、prompt snippets drawer、prompt helper、job history drawer、preview、gallery、lightbox 和 size dialog

前端构建命令：

```bash
npm --prefix frontend install
npm --prefix frontend run build
```

### 存储

运行时持久化存储非常简单：

- 生成的图片保存在 `images/` 目录
- Gallery 元数据、图片字节数、FTS 提示词索引和 API 预设保存在 SQLite：`data/app.sqlite3`，包含真实图片宽高、`completed_at` 完成时间、北京时间生成完成时间和生成耗时
- 提示词收藏夹保存在 SQLite：`data/app.sqlite3`，独立于 Gallery 元数据
- 可选 R2 备份只做增量对象上传；本地 `images/` 文件和 SQLite Gallery 行仍是图片服务、缩略图、ZIP 导出和导入流程使用的唯一 Gallery 源
- 生成/编辑任务的状态、错误、耗时、`completed_at`、请求参数和结果元数据保存在 SQLite：`data/app.sqlite3`；多图任务会保留完整 `images` 结果列表，同时继续用第一张结果填充 `image_id`/`image_url` 以兼容旧客户端
- image unit 调度、lease 和父任务聚合保存在 SQLite，多 Granian worker 可以不引入 Redis 共享生成/编辑任务

### 生成流程

1. 前端调用 `/api/generate`
2. 后端校验配置，创建一个 SQLite 父任务，并按请求输出数量创建 image unit 子任务
3. SQLite 队列/并发限制会跨 Granian worker 全局生效，执行中通过 SSE 推送细分进度
4. `n > 1` 的生成请求仍只暴露一个父任务，但会按 `n` 计入队列容量，并通过 SQLite image-unit lease 分片执行；`/v1/images/generations` unit payload 固定使用 `n=1`
5. 上游返回数据解码/下载、校验并落盘，同时更新 Gallery 元数据
6. 任务历史可通过 `/api/generate/jobs*` 查询/订阅，可清空（`DELETE /api/generate/jobs/history`）或取消运行中任务；可选触发签名 webhook 回调

### 编辑流程

1. 前端选择编辑源（上传一张或多张图片，也可以组合一张 Gallery 图片）并调用 `/api/edits` 或 `/api/edits/from-gallery/{image_id}`
2. 后端创建任务并以 multipart 调用上游 `/v1/images/edits`
3. 源图片和支持参数会被转发；多参考图会以重复的 `image[]` 字段发给上游，不支持格式（如 SVG）会被拒绝
4. 通过 SSE 推送进度；返回数据解码/下载、校验并落盘
5. 编辑结果进入预览和 Gallery，沿用与生成一致的队列/历史/取消模型

## 技术栈

- Python 3.11+
- FastAPI
- Granian
- aiohttp
- SQLite
- Pydantic v2
- SvelteKit
- TypeScript
- Tailwind CSS

## 项目结构

```text
LICENSE
README.md
Dockerfile
docker-compose.yml
.env.example
VERSION
requirements.txt
package.json
backend/
  requirements-dev.txt
  app/
    main.py
    api/
    core/
    schemas/
    repositories/
    integrations/
    services/
  tests/
frontend/
  package.json
  svelte.config.js
  vite.config.ts
  src/
    routes/
    lib/
      api/
      stores/
      components/
      utils/
deploy/
  nginx.conf
images/
data/
```

## 快速开始

### 前置条件

需要以下条件之一：

- Python 3.11 或更新版本
- Docker
- Docker Compose

实际生成图像需要一个外部 GPT 兼容图像 API。

### Docker

直接 `docker run` 暴露的是 FastAPI/Granian 进程，不经过 nginx。这个路径仍由后端 `FileResponse` 返回图片，适合简单本地部署。

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 127.0.0.1:9090:9090 \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

如果解析 `python:3.11-slim` 时 Docker Hub 超时，可以改用可访问的镜像源：

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

### Docker Compose

Compose 会在后端前面放 nginx。浏览器入口仍是 `http://127.0.0.1:9090`；宿主端口由 nginx 暴露，API/index 回源 FastAPI，SvelteKit immutable assets 由 nginx 直接返回，Gallery 图片/缩略图/下载会先由后端完成访问校验和 Gallery 引用校验，再通过 nginx internal redirect 发送文件字节。

```bash
cp .env.example .env
# 按需修改 .env
# 默认必须设置 ACCESS_KEY，除非显式设置 ALLOW_UNAUTHENTICATED=true
docker-compose up -d --build --force-recreate
```

如果 Compose 构建时 Docker Hub 超时，先在 `.env` 里设置：

```bash
PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim
NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine
```

`GRANIAN_RUNTIME_THREADS` 默认是 `2`。CPU 资源更充足时，可以先试 `GRANIAN_RUNTIME_THREADS=4`。`GRANIAN_WORKERS>1` 已支持生成/编辑 fan-out，以及可跟踪 Gallery export/R2 sync job：worker 通过 SQLite 认领任务，SSE 轮询 SQLite 状态，取消会标记未完成的生成 unit，但不会跨进程强杀正在等待上游的调用。

### 本地开发

```bash
pip install -r backend/requirements-dev.txt
npm --prefix frontend install
npm run backend:dev
```

另开一个终端：

```bash
npm run frontend:dev
```

然后打开 `http://localhost:5173`。Vite dev server 会把 `/api` 和 `/health` 代理到 `127.0.0.1:9090`，浏览器侧仍然是同源路径。
默认只监听 `127.0.0.1`；如果要做局域网/外网调试，使用 `npm run frontend:dev -- --host 0.0.0.0`。

如果要单进程 smoke test，先构建前端，再通过 Granian 运行 FastAPI：

```bash
npm run frontend:build
granian --interface asgi backend.app.main:app --host 0.0.0.0 --port 9090 --reload
```

然后打开 `http://localhost:9090`。

若本地开发需要无鉴权启动，请设置 `ALLOW_UNAUTHENTICATED=true`。该模式启动时会输出 warning，因为未设置 `ACCESS_KEY` 时所有非健康检查 API 都不需要鉴权。

### 健康检查

```bash
curl http://localhost:9090/health
```

## 使用方法

1. 打开网站
2. 可用左上角语言按钮在英文/简体中文之间切换
3. 点击右上角齿轮图标
4. 选择已有预设，或点击 New 新建预设
5. 填写 API Base URL
6. 选择 API Path
7. 填写该预设的默认模型；Generate/Edit 表单里的 Model 默认值会使用当前预设的值
8. 选择该预设的默认 Response Format；Generate/Edit 表单里的 Response format 默认值会使用当前预设的值
9. 填写 API Key，或填写 `${OPENAI_API_KEY}` 这类环境变量引用；直接填写的 key 需要显式设置 `ALLOW_PLAINTEXT_SECRETS=true`
10. 可选：填写全局 SOCKS5 代理，或填写 `${UPSTREAM_PROXY_URL}` 这类环境变量引用
11. 可选：填写全局 Webhook URL，或填写 `${WEBHOOK_URL}` 这类环境变量引用，用于生成/编辑任务完成回调
12. 可选：配置 R2 备份的 endpoint URL、储存桶、prefix，以及 `${R2_ACCESS_KEY_ID}` / `${R2_SECRET_ACCESS_KEY}` 这类环境变量引用，然后点击测试 R2
13. 可选：配置提示词优化器的 endpoint URL、模型、超时时间和 API Key/环境变量引用；直接填写的 key 需要显式设置 `ALLOW_PLAINTEXT_SECRETS=true`
14. 可选：打开 Overall Config，编辑主 Settings 表单里没有覆盖的 `.env.example` 变量
15. 可选：对已保存预设执行 Health check
16. 点击 Save Preset
17. 输入提示词
18. 点击提示词助手标签追加常用修饰词
19. 点击 Optimize 通过服务端优化器改写提示词
20. 点击右上角提示词按钮保存或复用提示词片段；使用片段会替换当前提示词
21. 选择生成参数；需要逐次复用不同上游路径时可直接选择 API Path
22. 点击 Generate
23. 也可以上传一张或多张编辑参考图、在 Gallery/Lightbox 中选择 “Edit this image”，或两者组合；上传会追加到当前编辑源，Clear 会清空全部编辑源
24. 点击 Edits 执行图生图
25. 在 Gallery/Lightbox 使用 “Use prompt” 或 “Use all” 复用历史提示词或完整参数
26. 查看预览和 Gallery；点击 Gallery 的同步按钮可把本地 Gallery 中 R2 prefix 下缺失的图片上传到配置的储存桶

## 支持的 API Path

面板支持以下上游路径。API Base URL 可以不带 `/v1`，也可以带 `/v1`；例如 `https://api.example.com` 和 `https://api.example.com/v1` 都可以。

### `/v1/images/generations`

- 向 Images API 发送生成请求
- 从 `data[]` 读取图片数据

### `/v1/responses`

- 向 Responses API 发送生成请求
- 上游请求体只发送 `prompt` 和 `model`；UI 里的模型默认值来自当前预设
- 从 `output[]` 中类型为 `image_generation_call` 的项目读取 base64 图片数据
- 选择该路径时，界面中的尺寸、质量、格式、压缩率、数量和 response format 控件会被禁用

### `/v1/chat/completions`

- 发送兼容 OpenAI Chat Completions 的生成请求
- 上游请求体只发送 `model`、`messages` 和 `stream: false`
- 支持 `grok-imagine-image-lite` 以及其他通过 chat completion 消息返回图片 URL 或 base64 数据的图像模型
- 可从 JSON chat completions 或 `data:` SSE chunk 中读取图片输出，包括 `![image](https://...)` 这类 Markdown 图片链接
- 选择该路径时，界面中的尺寸、质量、格式、压缩率、数量和 response format 控件会被禁用

### `/v1/images/edits`

- 上传图片后点击 Edits、在 Gallery 里选择 “Edit this image” 后点击 Edits，或两者组合使用
- 始终在配置的 API Base URL 下调用 `/v1/images/edits`
- 使用 multipart/form-data 发送源图片字段和支持的编辑参数；单张上传使用 `image`，多参考图会以重复的 `image[]` 字段转发给上游
- 最多支持 16 张编辑参考图；本地上传会追加到当前编辑源列表，并可与一张 Gallery 源图组合
- 上传的源图必须是受支持的位图图片格式；SVG 上传会被拒绝
- 如果上游返回 `404`、`405` 或 `501`，界面会提示 `/v1/images/edits` 不受支持并停止编辑请求

## API 预设健康检查

- `POST /api/settings/presets/{preset_id}/health` 会校验已保存预设，不会发送真实生成请求
- 检查项包括 API Path 是否允许、HTTPS URL/hostname、上游 host allowlist、SSRF DNS/内网 IP 校验、API Key/环境变量引用是否可用，以及低成本 `OPTIONS`/`HEAD` 上游探测
- 上游探测只作为诊断信息：`OPTIONS 404/410` 会显示为 warning，因为很多 GPT-compatible 网关只实现 `POST`；如果只有 `upstream_probe` 是 error，整体预设健康状态仍显示正常
- 返回结构为 `{ status, checks: [{ name, status, message }] }`，状态值为 `ok`、`warning` 或 `error`
- API Key 环境变量引用必须使用完整的 `${ENV_VAR_NAME}` 格式；数据库只保存引用字符串，生成/编辑请求会在执行时从服务端环境变量解析真实值
- 默认拒绝直接把 API Key 明文持久化到 SQLite；只有显式设置 `ALLOW_PLAINTEXT_SECRETS=true` 才允许

## Overall Config

- Settings 里有 `Overall Config` 弹窗，用来管理主 Settings 表单未覆盖的 `.env.example` 变量。
- 服务启动时会把当前进程环境变量同步到 SQLite。生效优先级是 `SQLite override > 当前 env > 代码默认值`。
- 弹窗保存的是 SQLite override，不会改写 `.env` 文件；只要保留 `data/app.sqlite3`，容器重建后 override 仍保留。
- Secret 保存时会把真实 override 写入 SQLite，但 API 响应和 UI 只返回打码值。提交 `********` 会保留已有 secret override；`Reset to .env` 会清除该 override。
- 可热生效字段会立即更新内存配置。运行路径、访问/session 启动行为、队列并发和 Docker build image 会标记为需重启或仅构建期生效。
- `DATABASE_FILE`、`DATA_DIR`、`IMAGES_DIR`、`THUMBNAILS_DIR` 可以显示和保存，但当前进程不能不重启就迁移 SQLite 连接或存储目录。

## 上游 SOCKS5 代理

- Settings 抽屉提供一个全局 `SOCKS5 代理` 字段，不跟随 API 预设切换。
- 留空时生成/编辑上游 API 请求保持直连。
- 默认使用 `${UPSTREAM_PROXY_URL}` 这类环境变量引用。若直接填写 `socks5://host:port` 或 `socks5://user:pass@host:port`，需要显式设置 `ALLOW_PLAINTEXT_SECRETS=true`；保存后的代理密码会在 API 响应和 UI 中打码。
- 代理边界刻意收窄：只有生成/编辑的上游 API `POST` 请求会使用 SOCKS5。Preset health check、Webhook、版本检查、前端 `/api/*` 请求和上游返回的图片 URL 下载都保持直连。

## 全局 Webhook URL

- Settings 抽屉在 `SOCKS5 代理` 下方提供一个全局 `Webhook URL` 字段，不跟随 API 预设切换。
- 留空时不发送任务回调。
- 默认使用 `${WEBHOOK_URL}` 这类环境变量引用；若直接填写 webhook URL，需要显式设置 `ALLOW_PLAINTEXT_SECRETS=true`。
- 配置后，生成/编辑任务完成时会向该 HTTPS URL 发送签名 webhook 回调。
- 配置 Webhook URL 时需要设置 `WEBHOOK_SIGNING_SECRET`；保存后的 webhook URL 会在 API 响应和 UI 中打码。

## R2 Gallery 备份同步

- R2 Backup 是增量备份路径，不是远端 Gallery 存储。
- 可以用 `.env` 的 `R2_BACKUP_ENABLED=true` 加其他 `R2_*` 变量配置，也可以在 Web Settings 中保存配置。当 SQLite 里还没有保存过 R2 settings 时，启动会把当前 `.env` R2 值持久化进去，所以 Web Settings 会直接显示这些值；之后 Web Settings 保存的值优先。Web Settings 的凭据字段支持 `${ENV_VAR_NAME}` 引用；直接保存明文凭据需要 `ALLOW_PLAINTEXT_SECRETS=true`。
- Settings 里的测试 R2 会执行 `HeadBucket`、带 prefix 的 `ListObjectsV2`，并写入一个很小的 probe object；probe 清理失败会作为 warning 返回。
- Gallery 的同步按钮只上传 `R2_KEY_PREFIX` 下缺失的本地 Gallery filename；已存在对象会跳过，bucket 中额外对象不会删除或覆盖。
- 将 `R2_SYNC_INTERVAL_HOURS` 或 Web Settings 里的同步间隔设为正整数，可按相同增量逻辑定时同步。`0` 表示关闭自动同步，服务启动后会等待完整间隔再执行第一次定时同步。

## 图像尺寸模式

- `auto` — 默认值；让模型自动选择输出尺寸
- 比例预设 — 1K / 2K / 4K，支持比例 `1:1`、`4:3`、`3:4`、`16:9`、`9:16`、`21:9`
- 自定义宽高 — 会归一化到 16 的倍数，最大边 `3840px`，最大纵横比 `3:1`，像素总量在 `655360` 到 `8294400` 之间

## 生成选项

- Quality：`auto`、`low`、`medium`、`high`
- Format：`PNG`、`JPEG`、`WebP`
- Compression：`PNG` 不可用；`JPEG` 和 `WebP` 可设置 `0-100`
- Quantity：`1` 到 `10`；编辑时可以先清空，提交 Generate/Edit 时如果为空会自动回填为 `1`
- Response Format：默认使用当前预设的值（默认 `url`），仍可选 `none` 和 `b64_json`；`none` 会省略 `response_format` 参数

## 导入与上传限制

- 每张上传的编辑源图大小受 `MAX_FILE_SIZE_MB` 限制，必须通过 Pillow 完整解码校验和像素炸弹限制，且必须是受支持的位图格式（`.png`、`.jpg`、`.jpeg`、`.webp`、`.gif`、`.avif`、`.bmp`、`.heic`、`.heif`、`.ico`、`.tif`、`.tiff`）；SVG 会被拒绝
- `/api/import` 只接受由 `/api/download-all` 导出的 ZIP 归档
- 导入 ZIP 必须包含 `metadata.json`
- 所选图片 ZIP 下载遇到缺失图库行或缺失图片文件时，会在 `metadata.skipped` 中记录跳过项
- 导入 ZIP 会校验上传体积、文件数、解压总体积、metadata 大小、安全路径和压缩比
- 导入图片条目在存储前必须通过扩展名、Content-Type/文件魔数、完整解码和像素数量校验

## 环境变量

`.env.example` 中列出的变量都会同步到 SQLite 的 Overall Config。主 Settings 表单已覆盖的变量仍在原位置管理；Overall Config 弹窗显示其余运行时、安全、限制和构建相关配置。

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEFAULT_API_URL` | 空 | 预填 API Base URL；可以不带或带 `/v1` |
| `DEFAULT_API_KEY` | 空 | 预填 API Key；优先使用 `${OPENAI_API_KEY}` 这类环境变量引用，直接填写的 key 会以明文保存到 SQLite |
| `DEFAULT_API_PATH` | `/v1/images/generations` | 默认上游路径；支持 `/v1/images/generations`、`/v1/responses` 和 `/v1/chat/completions` |
| `DEFAULT_RESPONSES_MODEL` | `gpt-5.4` | 当请求/预设没有提供模型时，`/v1/responses` 使用的兜底顶层模型 |
| `DEFAULT_UPSTREAM_SOCKS5_PROXY` | 空 | 可选的全局 SOCKS5 代理默认值，仅用于生成/编辑的上游 API 请求 |
| `AIOHTTP_CONNECTION_LIMIT` | `100` | 上游/探测/下载 aiohttp connector 总连接数限制 |
| `AIOHTTP_CONNECTION_LIMIT_PER_HOST` | `20` | aiohttp connector 单 host 连接数限制；`0` 表示不限制单 host |
| `PROMPT_OPTIMIZER_ENABLED` | `false` | 是否启用服务端提示词优化器 |
| `PROMPT_OPTIMIZER_API_URL` | 空 | 提示词优化器使用的完整 Chat Completions 兼容 endpoint URL |
| `PROMPT_OPTIMIZER_API_KEY` | 空 | 提示词优化器 API Key；优先使用 `${OPENAI_API_KEY}` 这类环境变量引用，直接填写的 key 会以明文保存到 SQLite |
| `PROMPT_OPTIMIZER_MODEL` | `gpt-4o-mini` | 提示词优化器模型 |
| `PROMPT_OPTIMIZER_TIMEOUT_SECONDS` | `60` | 提示词优化请求超时时间（秒） |
| `PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS` | `4000` | 回填到文本框的优化后提示词最大长度 |
| `PROMPT_OPTIMIZER_MAX_RESPONSE_MB` | `8` | JSON 解析前允许的最大优化器上游响应体积（MB） |
| `PROMPT_OPTIMIZER_HOST_ALLOWLIST` | 空 | 可选的优化器 endpoint 主机名白名单，逗号分隔 |
| `R2_BACKUP_ENABLED` | `false` | 使用环境变量配置 R2 时，启用 Gallery 同步备份 |
| `R2_ENDPOINT_URL` | 空 | Cloudflare R2 S3 兼容 endpoint URL，例如 `https://ACCOUNT_ID.r2.cloudflarestorage.com` |
| `R2_BUCKET_NAME` | 空 | Gallery 同步备份使用的储存桶 |
| `R2_REGION` | `auto` | R2 使用的 S3 client region name |
| `R2_KEY_PREFIX` | `gallery/` | Gallery 备份对象 key prefix；手动验证建议使用 `gallery-test/` 这类独立 prefix |
| `R2_SYNC_INTERVAL_HOURS` | `0` | Gallery Sync 定时同步间隔，单位小时；`0` 表示关闭自动同步 |
| `R2_ACCESS_KEY_ID` | 空 | env-ref 凭据解析使用的 R2 access key ID |
| `R2_SECRET_ACCESS_KEY` | 空 | env-ref 凭据解析使用的 R2 secret access key |
| `APP_VERSION` | `VERSION` 文件 | 覆盖界面显示和 `/api/version` 返回的当前应用版本；每次请求实时读取 |
| `GITHUB_REPO` | `Z1rconium/gpt-image-linux` | 用于检测 latest release 新版本的 GitHub `owner/repo`；设为空可禁用最新版本检查 |
| `ENABLE_VERSION_CHECK` | `true` | 启用每次请求的 GitHub 最新版本检测，用于 Header 的 `New` 标记 |
| `VERSION_CHECK_TIMEOUT_SECONDS` | `3` | 每次请求 GitHub latest release 或分支 `VERSION` 的超时时间 |
| `VERSION_CHECK_BRANCH` | `main` | latest release 失败后，用于回退读取 raw `VERSION` 文件的分支 |
| `ENABLE_METRICS` | `false` | 启用 `/api/metrics` JSON counters/gauges/rates/延迟摘要和 Prometheus 文本输出 |
| `SLOW_GALLERY_QUERY_MS` | `200` | `/api/gallery` 达到该阈值时记录筛选条件、页码、total 和 DB 查询耗时 |
| `ENABLE_NGINX_ACCEL_REDIRECT` | `false` | nginx internal alias 在前置时，为已授权的图片/缩略图/下载响应返回 `X-Accel-Redirect`；Compose 默认启用 |
| `ACCESS_KEY` | 空 | 默认要求设置；设置后每个非健康路由均需解锁 |
| `ALLOW_UNAUTHENTICATED` | `false` | 设置为 `true` 可显式允许在未设置 `ACCESS_KEY` 时启动；启动时会输出 warning，因为非健康检查 API 不需要鉴权 |
| `ACCESS_KEY_COOKIE_NAME` | `gpt_image_access` | 访问会话使用的浏览器 cookie 名称 |
| `ACCESS_COOKIE_SECURE` | `true` | 给访问 cookie 添加 Secure；仅在纯 HTTP 本地/内网部署时设为 `false` |
| `ACCESS_MAX_FAILURES` | `5` | 触发临时锁定前允许的访问密钥失败次数 |
| `ACCESS_LOCKOUT_SECONDS` | `300` | 失败次数过多后的锁定时长（秒） |
| `IP_ALLOWLIST` | 空 | 允许访问的 IP/CIDR，逗号分隔 |
| `TRUST_PROXY_HEADERS` | `false` | 是否读取受信任反向代理的 `X-Forwarded-For`、`X-Real-IP`、`X-Forwarded-Proto` 或 `X-Forwarded-Host` |
| `TRUSTED_PROXY_IPS` | 空 | 允许提供可信代理头的代理 IP/CIDR，逗号分隔 |
| `PUBLIC_ORIGIN` | 空 | 可选的浏览器侧规范 origin，例如 `https://panel.example.com`；同时加入 Host 白名单并作为 CSRF expected origin |
| `ALLOWED_HOSTS` | 空 | 可选的 Host / 受信任 `X-Forwarded-Host` 白名单，逗号分隔；支持 hostname、`host:port` 或 origin |
| `CSRF_ORIGIN_CHECK_ENABLED` | `true` | 是否拒绝无法通过 `Origin`、`Referer` 或同源 `Sec-Fetch-Site` 证明来源的 `POST`、`PATCH`、`DELETE` 请求 |
| `UPSTREAM_HOST_ALLOWLIST` | 空 | 生成/编辑上游 API URL 的可选主机名白名单，逗号分隔；配置 SOCKS5 上游代理时，代理本身是远端 DNS/网络可达性的信任边界 |
| `WEBHOOK_HOST_ALLOWLIST` | 空 | 可选 webhook 主机名白名单，逗号分隔 |
| `MAX_FILE_SIZE_MB` | `50` | 上传为编辑源图的图片、导入图片文件和上游图片 URL 下载的最大体积（MB） |
| `MAX_JSON_BODY_MB` | `1` | 非上传 API 调用的最大 JSON 请求体积（MB） |
| `MAX_UPSTREAM_JSON_MB` | `128` | 解析前允许的最大上游 JSON/SSE 响应体积（MB）；大图或多图建议使用 `response_format=url` |
| `MAX_IMAGE_PIXELS` | `100000000` | Pillow 接受的最大解码图片像素数，超过后按 decompression bomb 拒绝 |
| `IMPORT_ARCHIVE_MAX_MB` | `1000` | `/api/import` 可上传 ZIP 的最大体积（MB） |
| `IMPORT_MAX_FILES` | `500` | 单个导入归档允许的最大文件数 |
| `IMPORT_MAX_UNCOMPRESSED_MB` | `1024` | 导入归档内所有文件解压后的最大总体积（MB） |
| `IMPORT_MAX_METADATA_BYTES` | `2097152` | 导入归档中 `metadata.json` 的最大字节数 |
| `IMPORT_MAX_COMPRESSION_RATIO` | `100` | 单个导入文件允许的最大解压/压缩体积比 |
| `GRANIAN_WORKERS` | `1` | Granian worker 进程数；设为 `>1` 时通过 SQLite 共享生成/编辑 image units |
| `MAX_ACTIVE_GENERATE_JOBS` | `2` | 全局允许同时运行的生成/编辑 image unit 数量 |
| `MAX_QUEUED_GENERATE_JOBS` | `20` | 超出并发后允许继续排队的 image unit 数量；超过后新请求返回 `429` |
| `IMAGE_JOB_UNIT_LEASE_SECONDS` | `120` | running image unit 的 SQLite claim lease，过期后其他 worker 可重试 |
| `IMAGE_JOB_UNIT_POLL_INTERVAL_SECONDS` | `0.35` | image-unit 调度和生成 SSE 的 SQLite 轮询间隔（秒） |
| `MAX_PENDING_EDIT_SOURCE_MB` | `200` | 待处理编辑源图的总字节上限（MB）；设为 `0` 可关闭该字节上限 |
| `MAX_SSE_SUBSCRIBERS_GLOBAL` | `200` | 全进程允许的最大活跃 SSE 订阅数 |
| `MAX_SSE_SUBSCRIBERS_PER_IP` | `10` | 单个客户端 IP 允许的最大活跃 SSE 订阅数 |
| `SSE_CONNECTION_TTL_SECONDS` | `3600` | 单条 SSE 连接的最长生命周期（秒） |
| `IMAGES_DIR` | `./images` | 图片存储目录 |
| `THUMBNAILS_DIR` | `./images/thumbs` | Gallery 缩略图生成目录 |
| `THUMBNAIL_MAX_SIDE` | `512` | 缩略图最大宽/高像素 |
| `ALLOW_PLAINTEXT_SECRETS` | `false` | 是否允许把 API Key / SOCKS5 代理 URL / Webhook URL / R2 凭据明文持久化到 SQLite；默认要求 `${ENV_VAR_NAME}` 引用 |
| `DATA_DIR` | `./data` | SQLite 运行时数据目录；启动时会收紧到 `0700` |
| `DATABASE_FILE` | `./data/app.sqlite3` | 保存 Gallery 元数据和 API 预设的 SQLite 数据库；启动时会收紧到 `0600` |
| `PYTHON_BASE_IMAGE` | `python:3.11-slim` | Docker 构建基础镜像；Docker Hub 慢或不可访问时可覆盖 |
| `NODE_BASE_IMAGE` | `node:24-alpine` | Docker 前端构建基础镜像；Docker Hub 慢或不可访问时可覆盖 |
| `WEBHOOK_SIGNING_SECRET` | 空 | 配置全局 Webhook URL 时需要；用于签名 webhook payload（`X-Webhook-Signature`） |
| `WEBHOOK_TIMEOUT_SECONDS` | `5` | 单次 webhook 投递超时时间（秒） |
| `WEBHOOK_MAX_ATTEMPTS` | `3` | webhook 最大重试次数 |

## 接口列表

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 前端页面 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/version` | 当前应用版本、配置的 GitHub 仓库和 latest release URL；设置 `ACCESS_KEY` 时需要先解锁 |
| `GET` | `/api/access/status` | 访问密钥会话状态 |
| `POST` | `/api/access` | 解锁访问 3 小时 |
| `POST` | `/api/settings` | 保存当前 API 预设 |
| `GET` | `/api/settings` | 获取当前设置和预设列表 |
| `GET` | `/api/settings/overall-config` | 获取 Overall Config 字段、打码 secret、来源和需重启/仅构建元数据 |
| `PUT` | `/api/settings/overall-config` | 保存或清除 Overall Config 字段的 SQLite override |
| `POST` | `/api/settings/r2/health` | 校验草稿 R2 Backup 配置并执行 bucket/list/write 检查 |
| `POST` | `/api/prompt/optimize` | 通过服务端提示词优化器改写提示词 |
| `GET` | `/api/prompt-snippets` | 查询提示词片段，可选 `query` 筛选 |
| `POST` | `/api/prompt-snippets` | 创建提示词片段 |
| `PATCH` | `/api/prompt-snippets/{snippet_id}` | 更新提示词片段标题、内容或收藏标记 |
| `DELETE` | `/api/prompt-snippets/{snippet_id}` | 删除提示词片段 |
| `POST` | `/api/settings/presets` | 新建并激活 API 预设 |
| `POST` | `/api/settings/presets/{preset_id}/activate` | 激活 API 预设 |
| `POST` | `/api/settings/presets/{preset_id}/health` | 校验已保存 API 预设并执行低成本上游探测 |
| `DELETE` | `/api/settings/presets/{preset_id}` | 删除 API 预设 |
| `POST` | `/api/generate` | 创建图像生成任务 |
| `POST` | `/api/edits` | 使用一张或多张 multipart 上传图片创建图像编辑任务 |
| `POST` | `/api/edits/from-gallery/{image_id}` | 使用已有 Gallery 图片创建图像编辑任务，可附加上传参考图 |
| `GET` | `/api/generate/jobs` | 查询排队/运行中的生成和编辑任务；传 `include_finished=true` 并可选 `limit`/`offset` 可分页查询持久化历史，传 `failed_only=true` 仅返回 `error`/`upstream_error` 历史行 |
| `GET` | `/api/generate/jobs/events` | 通过 SSE 推送排队/运行中的生成和编辑任务 |
| `DELETE` | `/api/generate/jobs/history` | 删除全部已结束的生成/编辑任务历史记录；保留排队/运行任务和 Gallery 图片 |
| `GET` | `/api/generate/{job_id}` | 查询任务状态或结果 |
| `GET` | `/api/generate/{job_id}/events` | 通过 SSE 推送单个任务状态和进度 |
| `DELETE` | `/api/generate/{job_id}` | 取消并移除排队/运行中的生成或编辑任务 |
| `GET` | `/api/gallery` | 分页查询 Gallery 图片，可选 `prompt`、`model`、`preset`、`size`、`date_from`、`date_to`、`favorite`、`include_total_bytes` 筛选 |
| `PATCH` | `/api/gallery/{id}/favorite` | 设置或取消 Gallery 收藏标记 |
| `GET` | `/api/gallery/{image_id}` | 按 ID 获取单个 Gallery 条目 |
| `GET` | `/api/image/{filename}` | 访问图片文件 |
| `GET` | `/api/thumb/{filename}` | 访问或懒生成 WebP Gallery 缩略图 |
| `GET` | `/api/download/{filename}` | 下载图片 |
| `DELETE` | `/api/gallery/{id}` | 删除 Gallery 条目和对应服务器图片文件 |
| `GET` | `/api/download-all` | 下载 Gallery 所有图片和 `metadata.json` 为 ZIP 文件 |
| `POST` | `/api/gallery/export-jobs` | 创建可跟踪进度的 Gallery ZIP 导出任务；可传 `ids` 导出所选图片 |
| `GET` | `/api/gallery/export-jobs/{job_id}` | 查询 ZIP 导出任务状态 |
| `GET` | `/api/gallery/export-jobs/{job_id}/events` | 通过 SSE 推送 ZIP 打包进度 |
| `GET` | `/api/gallery/export-jobs/{job_id}/download` | 下载已完成的 ZIP 导出，并通过 `Content-Length` 支持传输进度 |
| `POST` | `/api/gallery/sync-jobs` | 创建单活 R2 Gallery 备份同步任务 |
| `GET` | `/api/gallery/sync-jobs/{job_id}` | 查询 R2 同步任务状态 |
| `GET` | `/api/gallery/sync-jobs/{job_id}/events` | 通过 SSE 推送 R2 同步进度 |
| `POST` | `/api/import` | 导入 `/api/download-all` 创建的 ZIP |
| `DELETE` | `/api/gallery` | 删除所有 Gallery 条目和服务器图片文件 |
| `GET` | `/api/metrics` | 可选指标快照；默认 JSON，带 `Accept: text/plain` 时返回 Prometheus exposition format；仅在 `ENABLE_METRICS=true` 时可用 |
| `GET` | `/api/metrics/prometheus` | 可选 Prometheus exposition metrics；仅在 `ENABLE_METRICS=true` 时可用 |

## 运行时注意事项

- 版本读取顺序是 `APP_VERSION` -> `VERSION`；本地版本和可选 GitHub 远端检查都会在访问解锁后、每次 Web 端触发版本请求时实时计算。远端检查会先读 latest release，再回退到配置分支的 `VERSION`，仅用于显示 `New`，不会阻塞使用。
- 配置 `PUBLIC_ORIGIN` 或 `ALLOWED_HOSTS` 后，未知 `Host` 或受信任 `X-Forwarded-Host` 会在 CSRF 检查前被拒绝；有副作用的浏览器请求必须带 `Origin`、`Referer` 或同源 `Sec-Fetch-Site`，且 `Sec-Fetch-Site: same-origin` 不能绕过 Host 白名单
- 上游返回的图片 URL 必须使用 HTTPS；后端发起下载前会拒绝 plain HTTP 图片 URL
- 配置上游 SOCKS5 代理时，后端仍会在连接前验证上游 URL，并在主机名本地解析到私有/内部 IP 时记录 warning；但远端 DNS 和网络可达性由 SOCKS5 代理决定，代理本身就是信任边界
- 预设、提示词收藏夹和 Gallery/Job 数据只保存在 `DATABASE_FILE`
- SQLite 仓储操作使用短连接，并在启动时启用 WAL；`DATA_DIR` 会 chmod 为 `0700`，SQLite 数据库和 sidecar 文件会 chmod 为 `0600`；应用 shutdown 和测试 reset 会调用 storage close hook，连接生命周期保持显式
- 生成与编辑共用按 image units 计量的 SQLite 队列（`MAX_ACTIVE_GENERATE_JOBS` + `MAX_QUEUED_GENERATE_JOBS`）；所有编辑源图先落到 `DATA_DIR/edit-sources` 并额外受 `MAX_PENDING_EDIT_SOURCE_MB` 总量限制；支持取消，并持久化终态历史（含 `completed_at`）
- 批量生成/编辑（`n > 1`）会占用 `n` 个队列单位；父任务会把成功 unit 结果聚合到 `images[]`，Gallery 元数据保留用户请求的 `n`，不同 unit 可被不同 Granian worker 认领执行
- 可跟踪 Gallery export job 和手动/定时 R2 sync job 持久化在 SQLite；`GRANIAN_WORKERS>1` 下创建、查询、SSE、下载都可跨进程工作；worker 通过 SQLite lease 认领 queued 或 lease 过期的 running job，导出 ZIP 存在共享 `DATA_DIR/exports`
- 提示词优化器使用独立的服务端 Chat Completions 兼容 endpoint 配置和用户可配置请求超时/响应体积上限，在后端解析 API Key 环境变量引用，不占用生成/编辑任务队列容量。
- R2 Backup 通过 boto3 访问 Cloudflare R2 S3 兼容 endpoint，并用 `asyncio.to_thread` 隔离阻塞调用；同步任务只列出配置的 prefix，只上传缺失的本地 Gallery filename，不会从 R2 服务、覆盖或删除 Gallery 图片。
- SSE 是主进度通道；`/api/generate/jobs` 提供列表/历史（`include_finished=true`，可选 `limit`/`offset`，可选 `failed_only=true`），`/api/generate/jobs/history` 清空终态历史，`/api/generate/jobs/events` 通过 SQLite 亚秒级轮询推送实时任务列表变化
- 任务终态历史包含 `stage_timings`：`upstream_wait`、`download_decode`、`validate`、`thumbnail`、`db_insert`；慢 Gallery 查询日志只记录提示词是否存在/长度/hash，以及其他筛选条件与 total，并计入 metrics；可选 metrics 包含队列深度、运行中任务数、失败率、任务分段耗时和慢 SQLite 查询数；终态状态区分 `cancelled`、`interrupted` 和 `upstream_error`，同时保留通用 `error`
- 上游 JSON/SSE 响应会在解析前受 `MAX_UPSTREAM_JSON_MB` 限制，JSON 请求体受 `MAX_JSON_BODY_MB` 限制；上游图片 URL 下载会做 SSRF/重定向目标复核，并会经过 Pillow 完整解码、`MAX_IMAGE_PIXELS` 像素限制和 `MAX_FILE_SIZE_MB` 体积限制
- `/api/import` 做 ZIP 安全与体积校验；`/api/download-all` 保留低内存流式导出，带进度的导出任务会写入临时 ZIP，让 UI 同时展示打包和传输进度
- Gallery 持久化图片字节数和缩略图（`THUMBNAILS_DIR`），旧图按需懒补缩略图；使用 Compose 时，nginx 会直接返回 immutable 前端资源，并且只在 FastAPI 返回已授权的 `X-Accel-Redirect` 后发送图片/缩略图/下载文件字节
- 启动时会清理缺失文件对应的 Gallery 记录，并把上次进程遗留的 running/queued 任务标记为 interrupted

## 测试

```bash
npm run frontend:check
npm run frontend:build
python3 -m pytest backend/tests/test_contract.py -q
npm run test:e2e
RUN_PERFORMANCE_TESTS=true python3 -m pytest backend/tests/test_performance.py -q
npm run test:e2e:perf
```

契约测试覆盖冻结的公共 API 表面，包括访问 cookie、settings、generation/edit 任务创建、任务耗时指标、SSE 响应 framing、Gallery import/export、前端入口/CSP 处理、静态资源访问、下载、422 校验错误和 500 错误形状。Playwright 覆盖访问门禁、设置抽屉焦点行为、mock 生成/编辑流程、Gallery 筛选/页码跳转/批量操作、toast live region 和 Lightbox 键盘关闭/导航。

## 贡献

欢迎贡献。

建议遵循以下原则：

- 后端修改尽量保持简单和明确
- 用户可见变更或值得发布的修复应同步更新 `VERSION`，格式为 `vMAJOR.MINOR.PATCH`
- 尽量使用 `backend/app/schemas/` 中的 FastAPI 响应模型
- 持久化存储操作集中在 `backend/app/repositories/`
- 上游 API 调用集中在 `backend/app/integrations/`
- 浏览器请求保持同源 `/api/*` 路径，不要引入前端直连跨域后端
- 不要在仓库文件中保存真实 API Key
- 除非明确要求，否则不要提交生成图片或运行时 Gallery 元数据
- 除非明确要求改变任务生命周期，否则保留现有异步生成与 SSE 进度机制

## 许可证

本项目采用 `CC BY-NC 4.0` 许可证，即 `Creative Commons Attribution-NonCommercial 4.0 International`。

- 允许任何人使用、复制、修改、再分发以及二次创作。
- 需要保留署名，并附带许可证说明。
- 不允许将本项目或其衍生作品用于商业用途。
- 如需商业使用，必须事先获得著作权人的许可。

许可证全文见 [LICENSE](./LICENSE)。
