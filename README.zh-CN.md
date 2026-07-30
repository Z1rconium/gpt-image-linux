<div align="center">
  <br />
  <img src="frontend/static/favicon.svg" alt="GPT Image Panel 标志" width="128" height="128" />

  <h1>GPT Image Panel</h1>

  <hr />

  <p><strong>自托管 GPT 兼容图像生成和编辑面板。</strong></p>

  <p>
    <a href="./README.md#english">English</a> ·
    简体中文 ·
    <a href="./README.zh-TW.md">繁體中文</a>
  </p>
</div>

## 概述

GPT Image Panel 是一个轻量级 Web UI，用于图像生成、图像编辑、图库管理和本地持久化。它连接用户配置的外部 GPT 兼容图像 API，并把图片与元数据保存到自己的服务器。

本项目只是自托管控制面板，不提供、不代理、不转售、不修改任何上游模型/API 服务。实际生成能力、计费、账号权限、内容政策和模型行为都来自你配置的上游服务商。

## 功能

- 支持 `/v1/images/generations`、`/v1/responses`、OpenAI 兼容 `/v1/chat/completions` 图像生成。
- 支持 `/v1/images/edits` 图像编辑，可用上传参考图或 Gallery 图片作为源图。
- API 预设管理：base URL/path/key、默认模型、response format、健康检查、SOCKS5 代理、webhook、环境变量引用式密钥。
- Web 管理的 Overall Config，显示 env/default/override 来源，以及需要重启或只影响构建的配置标记。
- 提示词助手、提示词片段、可选服务端提示词优化器，以及用于提示词改写/检查/变体、参数推荐、任务诊断、编辑规划和 Gallery 图片分析的 AI Assistant 子系统。
- SQLite 任务队列：SSE 进度、取消、重试/复用、历史记录、阶段耗时、生成/编辑共享并发限制。
- 本地 Gallery：游标分页、搜索/筛选、收藏、Lightbox、selection token 批量操作、ZIP 导入导出、缩略图、大小统计、异步导出/导入任务。
- 可选 Cloudflare R2 Gallery 备份同步；本地 SQLite 和图片文件仍是唯一源数据。
- 访问密钥、IP/Host 白名单、可信反向代理头、CSRF 检查、CSP nonce、版本检查、可选 JSON/Prometheus metrics。

## 架构

- 后端：`backend/app/` 下的 FastAPI；ASGI 入口是 `backend.app.main:app`。
- 前端：`frontend/` 下的 SvelteKit 静态应用；生产后端服务 `frontend/build/`。
- 运行时存储：图片默认在 `images/`，缩略图在 `images/thumbs/`，SQLite 数据在 `data/app.sqlite3`，日志在 `data/logs/`。
- 公共 API 路由：`backend/app/api/contract_app.py`。
- DTO：`backend/app/schemas/`。
- 持久化：`backend/app/repositories/`。
- 上游 API：`backend/app/integrations/`。
- 运行配置：`backend/app/core/settings.py`、`backend/app/core/overall_config.py`、`.env.example` 和 `docker-compose.yml`。

## 技术栈

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

## 快速开始

### Docker Compose

```bash
cp .env.example .env
# 修改 .env：至少设置 ACCESS_KEY，并按需填默认上游 API
# 此示例通过回环地址使用 HTTP，需要禁用 Secure cookie
ACCESS_COOKIE_SECURE=false docker-compose up -d --force-recreate
```

打开 `http://127.0.0.1:9090`。

此本地 HTTP 示例需要设置 `ACCESS_COOKIE_SECURE=false`；通过 HTTPS 提供服务时应保持为 `true`。默认必须设置 `ACCESS_KEY`。仅本地测试时，清空 `ACCESS_KEY` 并设置 `ALLOW_UNAUTHENTICATED=true`，这会让所有非 health API 都不需要鉴权。

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

Docker Hub 慢或不可访问时：

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

### 本地开发

先使用本机的 Python 3.11+ 创建项目专用虚拟环境。`.venv` 属于本地开发环境，仓库不提供该目录。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
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
ALLOW_UNAUTHENTICATED=true .venv/bin/granian --interface asgi backend.app.main:app --host 127.0.0.1 --port 9090
```

## 配置

大多数运行时配置在 `.env.example`。API 预设、提示词优化器、R2 备份和部分应用/运行时配置也可通过 Web Settings / Overall Config 管理。关键变量：

| 变量 | 用途 |
| --- | --- |
| `ACCESS_KEY` | 访问密钥。除非清空该变量并设置 `ALLOW_UNAUTHENTICATED=true`，否则必填。 |
| `DEFAULT_API_URL` | 默认上游 API base URL，可带或不带 `/v1`。 |
| `DEFAULT_API_KEY` | 默认上游 API key。Web Settings 中建议用 `${OPENAI_API_KEY}` 这类 env ref。 |
| `DEFAULT_API_PATH` | `/v1/images/generations`、`/v1/responses` 或 `/v1/chat/completions`。 |
| `DEFAULT_RESPONSES_MODEL` | `/v1/responses` 在请求/预设未提供模型时使用的 fallback 模型。 |
| `AIOHTTP_CONNECTION_LIMIT` / `AIOHTTP_CONNECTION_LIMIT_PER_HOST` | 上游请求、探测、下载共用的 aiohttp connector 限制。 |
| `APP_VERSION` / `GITHUB_REPO` / `ENABLE_VERSION_CHECK` | UI/API 版本显示和 latest release 检查。 |
| `VERSION_CHECK_CACHE_SECONDS` | 每个进程成功版本检查的缓存时间。 |
| `MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB` / `UPSTREAM_MEMORY_BUDGET_MB` | 单任务解码图片上限和进程内上游内存加权准入预算。 |
| `DB_EXECUTOR_WORKERS` / `SQLITE_BUSY_*` | SQLite 专用执行器大小，以及短超时和抖动重试参数。 |
| `IMAGE_CPU_CONCURRENCY` / `FILE_IO_CONCURRENCY` | 每个进程完整图片解码和阻塞文件 I/O 的有界并发数。 |
| `IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS` | 合并 image unit 进度写入的最小间隔。 |
| `RUNTIME_METRICS_REFRESH_SECONDS` / `EVENT_LOOP_LAG_SAMPLE_SECONDS` | 后台协调快照和事件循环延迟采样间隔。 |
| `MAX_ACTIVE_GENERATE_JOBS` | 全局运行中的生成/编辑 image unit 上限。 |
| `MAX_QUEUED_GENERATE_JOBS` | 队列容量，超过后新任务返回 `429`。 |
| `MAX_PENDING_EDIT_SOURCE_MB` | 全局待处理编辑源图片字节预留上限。 |
| `MAX_SSE_SUBSCRIBERS_GLOBAL` / `MAX_SSE_SUBSCRIBERS_PER_IP` / `SSE_CONNECTION_TTL_SECONDS` | SSE slot 限制和最大连接生命周期。 |
| `IMAGES_DIR` | 图片保存目录。 |
| `THUMBNAILS_DIR` / `THUMBNAIL_*` | Gallery 缩略图存储和生成控制。 |
| `DATA_DIR` / `DATABASE_FILE` | SQLite 运行时数据。 |
| `PROMPT_OPTIMIZER_*` | 可选提示词优化器配置。 |
| `AI_ASSISTANT_*` | AI Assistant 默认启用；如需关闭，设置 `AI_ASSISTANT_ENABLED=false`。API URL、密钥、文本模型、超时、路径和 host allowlist 复用 `PROMPT_OPTIMIZER_*`。`AI_ASSISTANT_MAX_CONCURRENCY` 限制并发上游 Assistant 调用，`AI_ASSISTANT_BATCH_MAX_IMAGES` 限制单次 Gallery AI 批量分析图片数。 |
| `R2_*` | 可选 Cloudflare R2 Gallery 备份配置；自定义 endpoint host 需要配置 `R2_ENDPOINT_HOST_ALLOWLIST`。 |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | 反向代理 Host/CSRF 加固。 |
| `ENABLE_NGINX_ACCEL_REDIRECT` / `PUBLIC_IMAGE_BASE_URL` / `PUBLIC_THUMBNAIL_BASE_URL` | 可选 nginx/CDN 图片字节服务行为。 |
| `GRANIAN_*` | 生产运行时进程、线程和静态资源调优。 |
| `ENABLE_METRICS` | 启用 JSON/Prometheus metrics 接口。 |
| `LOG_DIR` / `LOG_LEVEL` / `LOG_RETENTION_HOURS` | 后端日志输出到 stdout 和轮转文件，默认保留 24 小时。 |

Secret 字段优先使用 `${ENV_VAR_NAME}` 引用。若要把明文 secret 写入 SQLite，必须显式设置 `ALLOW_PLAINTEXT_SECRETS=true`。

Overall Config 会把 override 持久化到 SQLite。部分配置可热更新；需要重启或只影响构建的配置会在 UI 中标记，可复现部署仍建议通过 `.env`/Compose 管理。

## 使用

1. 打开面板。
2. 如启用访问密钥，先用 `ACCESS_KEY` 解锁。
3. 打开 Settings。
4. 创建或选择 API 预设。
5. 设置 API base URL、API path、模型、response format 和 API key/env ref。
6. 按需配置 SOCKS5 代理、webhook、提示词优化器、AI Assistant、R2 备份或 Overall Config override。
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
| `GET` | `/api/access/status` | 读取访问状态。 |
| `POST` | `/api/access` | 使用访问密钥解锁面板。 |
| `GET/POST/DELETE` | `/api/access/admin`, `/api/access/admin/status` | 读取、解锁或清除受保护设置使用的管理员访问状态。 |
| `GET` | `/api/version`, `/api/version/latest` | 读取当前版本和可选最新 release 信息。 |
| `GET/PUT` | `/api/settings/overall-config` | 读取/保存 Overall Config override。 |
| `GET/POST` | `/api/settings` | 读取/保存当前预设、提示词优化器、R2 备份、代理和 webhook 设置。 |
| `POST` | `/api/settings/presets` | 创建 API 预设。 |
| `POST` | `/api/settings/presets/{preset_id}/activate` | 激活已保存 API 预设。 |
| `DELETE` | `/api/settings/presets/{preset_id}` | 删除 API 预设。 |
| `POST` | `/api/settings/presets/{preset_id}/health` | 校验已保存上游预设。 |
| `POST` | `/api/settings/r2/health` | 校验草稿 R2 备份设置。 |
| `GET/POST` | `/api/prompt-snippets` | 查询/创建提示词片段。 |
| `POST` | `/api/prompt-snippets/search` | 搜索可复用提示词片段。 |
| `PATCH/DELETE` | `/api/prompt-snippets/{snippet_id}` | 更新/删除提示词片段。 |
| `GET/POST` | `/api/prompt/optimizer-system-prompt` | 读取/保存提示词优化器 system prompt。 |
| `POST` | `/api/prompt/optimize`, `/api/prompt/optimizer-health` | 优化提示词或探测优化器连通性。 |
| `POST` | `/api/assistant/health` | 探测 AI Assistant 连通性。 |
| `POST` | `/api/assistant/prompt/rewrite`, `/api/assistant/prompt/check`, `/api/assistant/prompt/variants` | Prompt Copilot 改写、检查和变体工具。 |
| `POST` | `/api/assistant/generate/recommend-params` | 仅推荐当前 API path 支持的生成参数。 |
| `POST` | `/api/assistant/jobs/{job_id}/diagnose`, `/api/assistant/edit/plan` | 诊断任务或生成编辑规划，不会自动提交。 |
| `POST` | `/api/assistant/image/prompt` | 在内存中校验并反推一张本地位图；返回可用于生成的提示词，不创建 Gallery 记录。 |
| `POST` | `/api/assistant/image/prompt/optimize` | 结合上传的源图优化反推提示词结果。 |
| `POST/GET` | `/api/assistant/gallery/*` | 描述、反推 prompt、分析、批量分析和读取本地 Gallery AI metadata。 |
| `POST` | `/api/generate` | 创建生成任务。 |
| `POST` | `/api/edits` | 用上传源图创建编辑任务。 |
| `POST` | `/api/edits/from-gallery/{image_id}` | 用 Gallery 图片创建编辑任务。 |
| `GET` | `/api/generate/jobs` | 查询实时任务和可选历史。 |
| `GET` | `/api/generate/jobs/events` | 任务列表 SSE。 |
| `GET/DELETE` | `/api/generate/{job_id}` | 读取或取消单个生成/编辑任务。 |
| `GET` | `/api/generate/{job_id}/events` | 单任务 SSE。 |
| `DELETE` | `/api/generate/jobs/history` | 清理终态任务历史。 |
| `GET` | `/api/gallery` | 查询/搜索/筛选 Gallery。 |
| `POST` | `/api/gallery/search` | 使用 JSON 请求体搜索/筛选 Gallery。 |
| `GET/DELETE` | `/api/gallery/{image_id}` | 读取或删除 Gallery 图片。 |
| `PATCH` | `/api/gallery/{image_id}/favorite` | 收藏/取消收藏单张 Gallery 图片。 |
| `POST/PATCH` | `/api/gallery/batch/*` | selection token、收藏、删除和下载等批量操作。 |
| `POST` | `/api/gallery/export-jobs`, `/api/gallery/direct-export-jobs` | 创建异步 Gallery 导出任务。 |
| `GET` | `/api/gallery/export-jobs/{job_id}`, `/api/gallery/direct-export-jobs/{job_id}` | 读取异步 Gallery 导出任务状态。 |
| `GET` | `/api/gallery/export-jobs/{job_id}/events`, `/api/gallery/direct-export-jobs/{job_id}/events` | Gallery 导出任务 SSE。 |
| `GET` | `/api/gallery/export-jobs/{job_id}/download` | 下载已完成的受跟踪导出 ZIP。 |
| `POST` | `/api/gallery/sync-jobs` | 创建 R2 备份同步任务。 |
| `GET` | `/api/gallery/sync-jobs/{job_id}`, `/api/gallery/sync-jobs/{job_id}/events` | 读取或订阅 R2 备份同步任务状态。 |
| `GET` | `/api/gallery/import-jobs/{job_id}` | 读取异步导入任务状态。 |
| `GET` | `/api/gallery/import-jobs/{job_id}/events` | 异步导入任务 SSE。 |
| `GET` | `/api/image/{filename}` | 返回鉴权后的图片字节。 |
| `GET` | `/api/thumb/{filename}` | 返回 Gallery 缩略图。 |
| `GET` | `/api/download/{filename}` | 下载单张 Gallery 图片。 |
| `GET` | `/api/download-all` | 流式导出 Gallery ZIP。 |
| `POST` | `/api/import` | 导入 Gallery ZIP；`async_job=true` 会创建导入任务。 |
| `GET` | `/api/metrics`, `/api/metrics/prometheus` | `ENABLE_METRICS=true` 时可用。 |

公共 API 已有契约测试；除非明确做 breaking change，否则保持路径、方法、状态码、SSE 事件名、cookie 和响应结构稳定。

## 贡献者边界

- 浏览器请求保持同源 `/api/*`；不要在前端直接调用上游模型 API、R2、webhook 目标或任意图片 URL。
- 保持现有代码分层：
  - 路由与请求编排在 `backend/app/api/routers/`
  - DTO 在 `backend/app/schemas/`
  - 持久化与 SQLite 协调在 `backend/app/repositories/`
  - 上游集成在 `backend/app/integrations/`
  - 前端镜像 API 类型在 `frontend/src/lib/api/types.ts`
- 除非明确要做 breaking change，否则保持这些公共契约稳定：
  - API 路径、方法、状态码、cookie、SSE 事件名、响应结构
  - 生成/编辑队列生命周期、取消语义、多 worker 下的 SQLite 协调
- 校验与安全逻辑保持集中：
  - 图片字节校验、安全路径、缩略图/归档 helper
  - SSRF 敏感 URL 处理继续放在 validators、safe connector、integration client 中
  - 前端可见 secret 只能是打码值或 env-ref 元数据
- 保持现有运行时约束：
  - 编辑任务最多接受 16 张 raster 源图
  - Gallery ZIP 导入导出继续沿用现有安全限制
  - SSE 使用 SQLite slot lease、全局/单 IP 限制和连接 TTL
  - R2 同步只是备份；本地 SQLite 记录和本地图片文件始终是源数据
- 新增或修改环境变量时，同步更新 `backend/app/core/settings.py`、用户可见时的 `backend/app/core/overall_config.py`、`.env.example`、可由 Compose 配置时的 `docker-compose.yml`，以及本 README。
- 不要提交运行时/生成产物，例如 `images/`、`data/`、`frontend/build/`、`.svelte-kit/`、Playwright 报告、测试结果、依赖目录、本地 DB 文件或日志。

## 测试

运行后端或契约测试前，请先激活项目本地 `.venv`。npm 的契约/性能测试脚本会使用 `.venv/bin/python`。

```bash
npm run frontend:check
npm run frontend:build
.venv/bin/python -m pytest backend/tests -q
npm run test:contract
npm run test:e2e
npm run test:perf
npm run test:e2e:perf
```

普通改动跑相关子集即可；大范围或发布前改动跑全套。

如果缺少 Playwright 浏览器：

```bash
npm --prefix frontend exec playwright install chromium
```

## 贡献

实现边界和贡献者需要遵守的不变量，以上面的 `贡献者边界` 为准。提交改动时只跑与你改动范围匹配的最小验证集合；如果改了行为或环境变量定义，README 和配置文件要一起更新。

## 许可证

本项目采用 `CC BY-NC 4.0`（`Creative Commons Attribution-NonCommercial 4.0 International`）许可证。

见 [LICENSE](./LICENSE)。
