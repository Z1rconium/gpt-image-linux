# GPT Image Panel

![FastAPI](https://img.shields.io/badge/FastAPI-0.109-009688?logo=fastapi)
![SvelteKit](https://img.shields.io/badge/SvelteKit-2-FF3E00?logo=svelte)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python)
![SQLite](https://img.shields.io/badge/SQLite-3-003B57?logo=sqlite)
![Docker](https://img.shields.io/badge/Docker-24.0+-2496ED?logo=docker)

Self-hosted GPT-compatible image generation and editing panel.

English | [中文](#中文文档)

## Overview

GPT Image Panel is a lightweight web UI for image generation, image editing, gallery management, and local persistence. It connects to an external GPT-compatible image API and stores images plus metadata on your own server.

This project is only a self-hosted control panel. It does not provide, proxy, resell, or modify any upstream model/API service. Generation capability, billing, account permissions, content policy, and model behavior all come from the upstream provider you configure.

## Features

- Image generation through `/v1/images/generations`, `/v1/responses`, or OpenAI-compatible `/v1/chat/completions`.
- Image editing through `/v1/images/edits`, including uploaded images and gallery-source edits.
- API presets with base URL/path/key, default model, response format, health checks, and env-ref secret support.
- Prompt helper tags, reusable prompt snippets, and optional server-side prompt optimizer.
- Job queue with SSE progress, cancellation, retry/reuse, persisted history, timing metadata, and shared generation/edit concurrency limits.
- Local gallery with search/filtering, favorites, lightbox navigation, batch actions, ZIP import/export, thumbnails, and optional byte-size metadata.
- Optional Cloudflare R2 gallery backup sync; local SQLite/images remain the source of truth.
- Access-key gate, IP/Host allowlists, proxy-header support, CSRF origin checks, CSP nonce injection, and optional metrics.

## Architecture

- Backend: FastAPI under `backend/app/`; ASGI entrypoint is `backend.app.main:app`.
- Frontend: SvelteKit static app under `frontend/`; production backend serves `frontend/build/`.
- Storage: generated images under `images/`; SQLite runtime data under `data/app.sqlite3`.
- Public API routing: `backend/app/api/contract_app.py`.
- DTOs: `backend/app/schemas/`.
- Persistence: `backend/app/repositories/`.
- Upstream API integration: `backend/app/integrations/`.
- Runtime config: `backend/app/core/settings.py` and `.env.example`.

## Tech Stack

- Python 3.11+
- FastAPI
- Granian
- aiohttp
- SQLite
- Pydantic v2
- SvelteKit
- TypeScript
- Tailwind CSS

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
deploy/
  nginx.conf
images/
data/
Dockerfile
docker-compose.yml
.env.example
requirements.txt
package.json
```

## Quick Start

### Docker Compose

```bash
cp .env.example .env
# edit .env: set ACCESS_KEY and any default upstream API values you want
docker-compose up -d --force-recreate
```

Open `http://127.0.0.1:9090`.

By default, `ACCESS_KEY` is required. For local-only testing you can set `ALLOW_UNAUTHENTICATED=true`, but that makes every non-health API route accessible.

### Docker

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 127.0.0.1:9090:9090 \
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

### Local Development

```bash
pip install -r backend/requirements-dev.txt
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
granian --interface asgi backend.app.main:app --host 0.0.0.0 --port 9090 --reload
```

## Configuration

Most runtime options live in `.env.example` and can also be managed through Web Settings / Overall Config. Important variables:

| Variable | Purpose |
| --- | --- |
| `ACCESS_KEY` | Access gate key. Required unless `ALLOW_UNAUTHENTICATED=true`. |
| `DEFAULT_API_URL` | Default upstream API base URL; may omit or include `/v1`. |
| `DEFAULT_API_KEY` | Default upstream API key. Prefer env refs such as `${OPENAI_API_KEY}` in Web Settings. |
| `DEFAULT_API_PATH` | `/v1/images/generations`, `/v1/responses`, or `/v1/chat/completions`. |
| `MAX_ACTIVE_GENERATE_JOBS` | Global running generation/edit image-unit limit. |
| `MAX_QUEUED_GENERATE_JOBS` | Queue capacity before new jobs return `429`. |
| `IMAGES_DIR` | Saved image directory. |
| `DATA_DIR` / `DATABASE_FILE` | SQLite runtime storage. |
| `PROMPT_OPTIMIZER_*` | Optional server-side prompt optimizer settings. |
| `R2_*` | Optional Cloudflare R2 gallery backup sync settings; custom endpoint hosts require `R2_ENDPOINT_HOST_ALLOWLIST`. |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | Reverse-proxy Host/CSRF hardening. |
| `ENABLE_METRICS` | Enables JSON/Prometheus metrics endpoints. |

Secret fields prefer `${ENV_VAR_NAME}` references. Literal secrets stored in SQLite require `ALLOW_PLAINTEXT_SECRETS=true`.

## Usage

1. Open the panel.
2. Unlock with `ACCESS_KEY` if enabled.
3. Open Settings.
4. Create or select an API preset.
5. Set API base URL, API path, model, response format, and API key/env ref.
6. Optionally configure SOCKS5 proxy, webhook, prompt optimizer, or R2 backup.
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
| `GET/POST` | `/api/settings` | Read/save active settings and presets. |
| `POST` | `/api/settings/presets/{preset_id}/health` | Validate a saved upstream preset. |
| `POST` | `/api/generate` | Start generation job. |
| `POST` | `/api/edits` | Start edit job with uploaded source images. |
| `POST` | `/api/edits/from-gallery/{image_id}` | Start edit job from an existing gallery image. |
| `GET` | `/api/generate/jobs` | List live jobs and optional persisted history. |
| `GET` | `/api/generate/jobs/events` | SSE stream for job-list updates. |
| `GET` | `/api/generate/{job_id}/events` | SSE stream for one job. |
| `GET` | `/api/gallery` | List/search/filter gallery images. |
| `GET` | `/api/image/{filename}` | Serve authorized image bytes. |
| `GET` | `/api/thumb/{filename}` | Serve generated gallery thumbnail. |
| `GET` | `/api/download-all` | Stream gallery ZIP export. |
| `POST` | `/api/import` | Import gallery ZIP archive. |
| `GET` | `/api/metrics` | Optional metrics when `ENABLE_METRICS=true`. |

The public API surface is contract-tested; keep paths, methods, status codes, SSE event names, cookies, and response shapes stable unless a breaking change is intentional.

## Runtime Notes

- Images and SQLite files are runtime data; do not commit `images/`, `data/`, `frontend/build/`, `.svelte-kit/`, Playwright reports, or logs.
- API keys, proxy URLs, webhook URLs, and R2 credentials are masked in API/UI responses.
- Uploaded/imported/downloaded images are byte-validated and decoded server-side; SVG upload is rejected for edits.
- Upstream image URL downloads are HTTPS-only and SSRF-aware.
- Generation/edit tasks share SQLite-backed queue/concurrency limits and work across multiple Granian workers.
- Gallery ZIP import/export uses safety limits from `.env.example`.
- R2 endpoints are HTTPS-only, SSRF-checked, and limited to `*.r2.cloudflarestorage.com` unless `R2_ENDPOINT_HOST_ALLOWLIST` names a custom host.
- R2 sync is backup-only; the app never serves, overwrites, or deletes local gallery images from R2.

## Testing

```bash
npm run frontend:check
npm run frontend:build
npm run test:contract
npm run test:e2e
npm run test:perf
npm run test:e2e:perf
```

Run the focused subset relevant to your change. For release-bound or broad changes, run all of them.

## Contributing

- Keep backend API contracts stable.
- Keep DTOs in `backend/app/schemas/`.
- Keep persistence in `backend/app/repositories/`.
- Keep upstream API calls in `backend/app/integrations/`.
- Keep browser calls same-origin through `/api/*`.
- Update `.env.example`, `README.md`, and `docker-compose.yml` when adding or changing environment variables.
- Avoid committing runtime/generated artifacts.

## License

This project is licensed under `CC BY-NC 4.0` (`Creative Commons Attribution-NonCommercial 4.0 International`).

See [LICENSE](./LICENSE).

---

# 中文文档

# GPT Image Panel

自托管 GPT 兼容图像生成和编辑 Web 面板。

[English](#gpt-image-panel) | 中文

## 概述

GPT Image Panel 是一个轻量级 Web UI，用于图像生成、图像编辑、图库管理和本地持久化。它连接用户配置的外部 GPT 兼容图像 API，并把图片与元数据保存到自己的服务器。

本项目只是自托管控制面板，不提供、不代理、不转售、不修改任何上游模型/API 服务。实际生成能力、计费、账号权限、内容政策和模型行为都来自你配置的上游服务商。

## 功能

- 支持 `/v1/images/generations`、`/v1/responses`、OpenAI 兼容 `/v1/chat/completions` 图像生成。
- 支持 `/v1/images/edits` 图像编辑，可用上传图片或 Gallery 图片作为源图。
- API 预设管理：base URL/path/key、默认模型、response format、健康检查、环境变量引用式密钥。
- 提示词助手、提示词片段、可选服务端提示词优化器。
- 任务队列：SSE 进度、取消、重试/复用、历史记录、阶段耗时、生成/编辑共享并发限制。
- 本地 Gallery：搜索/筛选、收藏、Lightbox、批量操作、ZIP 导入导出、缩略图、可选大小统计。
- 可选 Cloudflare R2 Gallery 备份同步；本地 SQLite 和图片文件仍是唯一源数据。
- 访问密钥、IP/Host 白名单、反向代理头、CSRF 检查、CSP nonce、可选 metrics。

## 架构

- 后端：`backend/app/` 下的 FastAPI；ASGI 入口是 `backend.app.main:app`。
- 前端：`frontend/` 下的 SvelteKit 静态应用；生产后端服务 `frontend/build/`。
- 存储：图片在 `images/`；SQLite 运行时数据在 `data/app.sqlite3`。
- 公共 API 路由：`backend/app/api/contract_app.py`。
- DTO：`backend/app/schemas/`。
- 持久化：`backend/app/repositories/`。
- 上游 API：`backend/app/integrations/`。
- 运行配置：`backend/app/core/settings.py` 和 `.env.example`。

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
deploy/
  nginx.conf
images/
data/
Dockerfile
docker-compose.yml
.env.example
requirements.txt
package.json
```

## 快速开始

### Docker Compose

```bash
cp .env.example .env
# 修改 .env：至少设置 ACCESS_KEY，并按需填默认上游 API
docker-compose up -d --force-recreate
```

打开 `http://127.0.0.1:9090`。

默认必须设置 `ACCESS_KEY`。仅本地测试时可以设 `ALLOW_UNAUTHENTICATED=true`，但这会让所有非 health API 都不需要鉴权。

### Docker

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  -p 127.0.0.1:9090:9090 \
  -v $(pwd)/images:/app/images \
  -v $(pwd)/data:/app/data \
  gpt-image-panel
```

Docker Hub 慢或不可访问时：

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

### 本地开发

```bash
pip install -r backend/requirements-dev.txt
npm --prefix frontend install
npm run backend:dev
```

另开终端：

```bash
npm run frontend:dev
```

打开 `http://localhost:5173`。Vite 会把 `/api` 和 `/health` 代理到 `127.0.0.1:9090`。

生产风格 smoke test：

```bash
npm run frontend:build
granian --interface asgi backend.app.main:app --host 0.0.0.0 --port 9090 --reload
```

## 配置

大多数运行时配置在 `.env.example`，也可通过 Web Settings / Overall Config 管理。关键变量：

| 变量 | 用途 |
| --- | --- |
| `ACCESS_KEY` | 访问密钥。除非 `ALLOW_UNAUTHENTICATED=true`，否则必填。 |
| `DEFAULT_API_URL` | 默认上游 API base URL，可带或不带 `/v1`。 |
| `DEFAULT_API_KEY` | 默认上游 API key。Web Settings 中建议用 `${OPENAI_API_KEY}` 这类 env ref。 |
| `DEFAULT_API_PATH` | `/v1/images/generations`、`/v1/responses` 或 `/v1/chat/completions`。 |
| `MAX_ACTIVE_GENERATE_JOBS` | 全局运行中的生成/编辑 image unit 上限。 |
| `MAX_QUEUED_GENERATE_JOBS` | 队列容量，超过后新任务返回 `429`。 |
| `IMAGES_DIR` | 图片保存目录。 |
| `DATA_DIR` / `DATABASE_FILE` | SQLite 运行时数据。 |
| `PROMPT_OPTIMIZER_*` | 可选提示词优化器配置。 |
| `R2_*` | 可选 Cloudflare R2 Gallery 备份配置；自定义 endpoint host 需要配置 `R2_ENDPOINT_HOST_ALLOWLIST`。 |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | 反向代理 Host/CSRF 加固。 |
| `ENABLE_METRICS` | 启用 JSON/Prometheus metrics 接口。 |

Secret 字段优先使用 `${ENV_VAR_NAME}` 引用。若要把明文 secret 写入 SQLite，必须显式设置 `ALLOW_PLAINTEXT_SECRETS=true`。

## 使用

1. 打开面板。
2. 如启用访问密钥，先用 `ACCESS_KEY` 解锁。
3. 打开 Settings。
4. 创建或选择 API 预设。
5. 设置 API base URL、API path、模型、response format 和 API key/env ref。
6. 按需配置 SOCKS5 代理、webhook、提示词优化器或 R2 备份。
7. 保存预设，必要时执行健康检查。
8. 输入 prompt 生成图片，或上传/选择源图执行编辑。
9. 在 Gallery 中复用参数、筛选、收藏、批量操作、导入导出或执行 R2 同步。

## 支持的上游路径

| 路径 | 说明 |
| --- | --- |
| `/v1/images/generations` | 标准图片生成接口，从 `data[]` 读取图片数据。 |
| `/v1/responses` | 发送 `prompt` 和 `model`，从 `image_generation_call` 输出项读取 base64 图片。 |
| `/v1/chat/completions` | 发送 OpenAI 兼容 chat completions 请求，从消息或 SSE chunk 中提取图片 URL/base64。 |
| `/v1/images/edits` | Edits 流程使用，发送 multipart 源图和支持的编辑参数。 |

使用 `/v1/responses` 和 `/v1/chat/completions` 时，尺寸、质量、格式、压缩率、数量控件会禁用，因为这些路径的参数契约不同。

## API 概览

核心后端路由：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查。 |
| `GET/POST` | `/api/settings` | 读取/保存设置和预设。 |
| `POST` | `/api/settings/presets/{preset_id}/health` | 校验已保存上游预设。 |
| `POST` | `/api/generate` | 创建生成任务。 |
| `POST` | `/api/edits` | 用上传源图创建编辑任务。 |
| `POST` | `/api/edits/from-gallery/{image_id}` | 用 Gallery 图片创建编辑任务。 |
| `GET` | `/api/generate/jobs` | 查询实时任务和可选历史。 |
| `GET` | `/api/generate/jobs/events` | 任务列表 SSE。 |
| `GET` | `/api/generate/{job_id}/events` | 单任务 SSE。 |
| `GET` | `/api/gallery` | 查询/搜索/筛选 Gallery。 |
| `GET` | `/api/image/{filename}` | 返回鉴权后的图片字节。 |
| `GET` | `/api/thumb/{filename}` | 返回 Gallery 缩略图。 |
| `GET` | `/api/download-all` | 流式导出 Gallery ZIP。 |
| `POST` | `/api/import` | 导入 Gallery ZIP。 |
| `GET` | `/api/metrics` | `ENABLE_METRICS=true` 时可用。 |

公共 API 已有契约测试；除非明确做 breaking change，否则保持路径、方法、状态码、SSE 事件名、cookie 和响应结构稳定。

## 运行时注意

- `images/`、`data/`、`frontend/build/`、`.svelte-kit/`、Playwright 报告和日志都属于运行/生成产物，不要提交。
- API key、代理 URL、webhook URL 和 R2 凭据会在 API/UI 响应中打码。
- 上传、导入、下载得到的图片会在服务端做字节校验和完整解码；编辑源不接受 SVG。
- 上游图片 URL 下载只接受 HTTPS，并做 SSRF 防护。
- 生成/编辑共享 SQLite 队列和并发限制，可跨多个 Granian worker 工作。
- Gallery ZIP 导入导出受 `.env.example` 中的安全限制约束。
- R2 endpoint 只接受 HTTPS，带 SSRF 校验，且默认限制为 `*.r2.cloudflarestorage.com`；自定义 host 需配置 `R2_ENDPOINT_HOST_ALLOWLIST`。
- R2 同步只是备份路径；应用不会从 R2 服务、覆盖或删除本地 Gallery 图片。

## 测试

```bash
npm run frontend:check
npm run frontend:build
npm run test:contract
npm run test:e2e
npm run test:perf
npm run test:e2e:perf
```

普通改动跑相关子集即可；大范围或发布前改动跑全套。

## 贡献

- 保持后端 API 契约稳定。
- DTO 放在 `backend/app/schemas/`。
- 持久化逻辑放在 `backend/app/repositories/`。
- 上游 API 调用放在 `backend/app/integrations/`。
- 浏览器请求保持同源 `/api/*`。
- 新增或修改环境变量时同步更新 `.env.example`、`README.md` 和 `docker-compose.yml`。
- 不提交运行时/生成产物。

## 许可证

本项目采用 `CC BY-NC 4.0`（`Creative Commons Attribution-NonCommercial 4.0 International`）许可证。

见 [LICENSE](./LICENSE)。
