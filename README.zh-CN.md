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

  <p>
    <img alt="CI 通过" src="https://img.shields.io/badge/CI-passing-2cc653?logo=github&logoColor=white" />
    <img alt="版本 v1.3.3" src="https://img.shields.io/badge/release-v1.3.3-0e8dcc" />
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" />
    <img alt="Node.js 24" src="https://img.shields.io/badge/Node.js-24-339933?logo=node.js&logoColor=white" />
    <img alt="FastAPI 0.115+" src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" />
    <img alt="SvelteKit 2" src="https://img.shields.io/badge/SvelteKit-2-FF3E00?logo=svelte&logoColor=white" />
    <img alt="许可证 CC BY-NC 4.0" src="https://img.shields.io/badge/License-CC_BY--NC_4.0-6f42c1" />
    <img alt="GHCR 镜像" src="https://img.shields.io/badge/GHCR-gpt--image--linux-1f6f8b?logo=github&logoColor=white" />
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
- 多 worker 协调：排队任务、后台 lease、SSE slot 和调度器所有权使用 SQLite lease。图片和缩略图文件的写入/删除仅使用进程内锁，并通过 UUID 文件名、原子 `Path.replace()` 和孤儿文件 GC TTL 清理容忍跨进程竞争。
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
# 创建不可登录的系统账户，不授予应用以外的权限
sudo useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin gpt-image
APP_UID="$(id -u gpt-image)"
APP_GID="$(id -g gpt-image)"
# 仅授予该账户运行期 bind mount 的访问权限
sudo install -d -o "$APP_UID" -g "$APP_GID" -m 0750 images data data/logs
sudo chown -R "$APP_UID:$APP_GID" images data
sed -i "s/^PUID=.*/PUID=$APP_UID/; s/^PGID=.*/PGID=$APP_GID/" .env
# 此示例通过回环地址使用 HTTP，需要禁用 Secure cookie
ACCESS_COOKIE_SECURE=false docker-compose up -d --force-recreate
```

打开 `http://127.0.0.1:9090`。

此本地 HTTP 示例需要设置 `ACCESS_COOKIE_SECURE=false`；通过 HTTPS 提供服务时应保持为 `true`。默认必须设置 `ACCESS_KEY`。仅本地测试时，清空 `ACCESS_KEY` 并设置 `ALLOW_UNAUTHENTICATED=true`，这会让所有非 health API 都不需要鉴权。

生产容器以 `.env` 中的 UID/GID（`PUID`/`PGID`）运行，且根文件系统为只读。上述命令会创建不可登录的 `gpt-image` 系统账户，并仅授予其 `images/`、`data/`（包括 `data/logs/`）的访问权限。应用目录保持只读；只有这些 bind mount 和受限的 `/tmp` tmpfs 可写。若账户已经存在，请跳过 `useradd` 命令并使用其 UID/GID。

### Docker

下方命令假设已按上方 Docker Compose 步骤创建不可登录的 `gpt-image` 账户，并设置了运行目录的属主。

```bash
docker build -t gpt-image-panel .
docker run -d --name gpt-image-panel \
  --user "$(id -u gpt-image):$(id -g gpt-image)" \
  --read-only \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=640m,mode=1777 \
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
  --build-arg NGINX_BASE_IMAGE=docker.m.daocloud.io/library/nginx:alpine \
  -t gpt-image-panel .
```

### Caddy 反向代理

Caddy 与应用运行在同一台主机时，使用占位域名，并继续将 `9090` 端口仅绑定到回环地址：

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:9090
}
```

通过 HTTPS 部署时，在 `.env` 中设置与域名匹配的应用来源和 Host 白名单：

```dotenv
PUBLIC_ORIGIN=https://panel.example.com
ALLOWED_HOSTS=panel.example.com
ACCESS_COOKIE_SECURE=true
TRUST_PROXY_HEADERS=true
TRUSTED_PROXY_IPS=127.0.0.1/32
```

`TRUSTED_PROXY_IPS` 必须标识 Caddy 连接应用时的实际对端。只有 Caddy 与应用运行在同一主机时才保留回环地址；容器化代理应填写 Caddy 容器或网络 CIDR。其他对端发送的代理头会被忽略。

只有一个上游时，推荐使用上面的基础反代配置。如果确实需要主动健康检查，请先确认 `/health` 在相同 `Host` 请求头下返回 `200`，然后使用复数形式的 `health_headers` 配置块：

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

健康检查响应状态码不在配置范围内时，Caddy 会将上游标记为不健康。只有一个上游时，这会导致请求失败，直到健康检查恢复。修改后验证并重载 Caddy：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy 自动 HTTPS 要求域名解析到服务器，并且入站 `80`、`443` 端口可访问。使用 Cloudflare 等 CDN 代理时，应确保边缘证书明确覆盖完整域名，特别是多级子域名；否则请求尚未到达 Caddy，浏览器就可能报告 `ERR_SSL_VERSION_OR_CIPHER_MISMATCH`。如果 Caddy 运行在容器内，`127.0.0.1` 指向 Caddy 容器自身，此时应改用应用服务名或其他可访问的容器网络地址。

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
| `ADMIN_KEY` | 设置管理的二次验证密钥。应设置为不同的值；省略时会回退到 `ACCESS_KEY`，并在启动日志中发出警告。 |
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
| `MAX_FILE_SIZE_MB` / `EDIT_UPLOAD_MAX_MB` / `IMPORT_ARCHIVE_MAX_MB` | 单张图片、编辑 multipart 总量和 Gallery 导入压缩包上限；编辑最多接受 8 个文件。 |
| `UPLOAD_INFLIGHT_MAX_MB` / `UPLOAD_INFLIGHT_PER_IP_MAX_MB` / `UPLOAD_RESERVATION_TTL_SECONDS` | 跨进程 SQLite 上传字节预约和过期 lease 清理。 |
| `ADMIN_MAX_FAILURES` / `ADMIN_LOCKOUT_SECONDS` | 独立管理员二次验证失败阈值和按客户端 IP 计算的锁定时长。 |
| `WEBHOOK_MAX_CONCURRENCY` / `WEBHOOK_QUEUE_MAX_SIZE` | 每个 Granian worker 进程的固定投递 worker 和待处理 webhook 队列；满队列会丢弃新投递并增加丢弃指标。 |
| `MAX_SSE_SUBSCRIBERS_GLOBAL` / `MAX_SSE_SUBSCRIBERS_PER_IP` / `SSE_CONNECTION_TTL_SECONDS` | SSE slot 限制和最大连接生命周期。 |
| `IMAGES_DIR` | 图片保存目录。 |
| `THUMBNAILS_DIR` / `THUMBNAIL_*` | Gallery 缩略图存储和生成控制。 |
| `DATA_DIR` / `DATABASE_FILE` | SQLite 运行时数据。 |
| `PROMPT_OPTIMIZER_*` | 可选提示词优化器配置。 |
| `AI_ASSISTANT_*` | AI Assistant 默认启用；如需关闭，设置 `AI_ASSISTANT_ENABLED=false`。API URL、密钥、文本模型、超时、路径和 host allowlist 复用 `PROMPT_OPTIMIZER_*`。`AI_ASSISTANT_MAX_CONCURRENCY` 限制并发上游 Assistant 调用，`AI_ASSISTANT_BATCH_MAX_IMAGES` 限制单次 Gallery AI 批量分析图片数。 |
| `R2_*` | 可选 Cloudflare R2 Gallery 备份配置；自定义 endpoint host 需要配置 `R2_ENDPOINT_HOST_ALLOWLIST`。 |
| `NODEIMAGE_API_KEY` | 可选 NodeImage API key，用于服务端 Gallery 图片上传；也可在 Web Settings 中配置 env ref。 |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | 反向代理 Host/CSRF 加固。 |
| `ENABLE_NGINX_ACCEL_REDIRECT` / `PUBLIC_IMAGE_BASE_URL` / `PUBLIC_THUMBNAIL_BASE_URL` | 可选 nginx/CDN 图片字节服务行为。 |
| `GRANIAN_*` | 生产运行时进程、线程和静态资源调优。 |
| `ENABLE_METRICS` | 启用 JSON/Prometheus metrics 接口。 |
| `LOG_DIR` / `LOG_LEVEL` / `LOG_RETENTION_HOURS` | 后端日志输出到 stdout 和轮转文件，默认保留 24 小时。 |

Secret 字段优先使用 `${ENV_VAR_NAME}` 引用。若要把明文 secret 写入 SQLite，必须显式设置 `ALLOW_PLAINTEXT_SECRETS=true`。

Overall Config 会把 override 持久化到 SQLite。部分配置可热更新；需要重启或只影响构建的配置会在 UI 中标记，可复现部署仍建议通过 `.env`/Compose 管理。

Webhook 并发和队列限制按进程生效。使用多个 `GRANIAN_WORKERS` 时，总投递并发和队列容量会随 worker 数量线性增加。

## 使用

1. 打开面板。
2. 如启用访问密钥，先用 `ACCESS_KEY` 解锁。
3. 打开 Settings。
4. 创建或选择 API 预设。
5. 设置 API base URL、API path、模型、response format 和 API key/env ref。
6. 按需配置 SOCKS5 代理、webhook、提示词优化器、AI Assistant、R2 备份、NodeImage 上传或 Overall Config override。
7. 保存预设，必要时执行健康检查。
8. 输入 prompt 生成图片，或上传/选择源图执行编辑。
9. 在 Gallery 中复用参数、筛选、收藏、批量操作、导入导出、执行 R2 同步或上传到 NodeImage。

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
  - 文件系统竞争通过 UUID 文件名、原子替换和孤儿文件 GC 来容忍；不要依赖进程内锁实现跨 worker 互斥
- 校验与安全逻辑保持集中：
  - 图片字节校验、安全路径、缩略图/归档 helper
  - SSRF 敏感 URL 处理继续放在 validators、safe connector、integration client 中
  - 前端可见 secret 只能是打码值或 env-ref 元数据
- 保持现有运行时约束：
  - 编辑任务最多接受 8 张 raster 源图
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
