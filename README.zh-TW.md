<div align="center">
  <br />
  <img src="frontend/static/favicon.svg" alt="GPT Image Panel 標誌" width="128" height="128" />

  <h1>GPT Image Panel</h1>

  <hr />

  <p><strong>自架式 GPT 相容圖片生成與編輯面板。</strong></p>

  <p>
    <a href="./README.md#english">English</a> ·
    <a href="./README.zh-CN.md">簡體中文</a> ·
    繁體中文
  </p>

  <p>
    <img alt="CI 通過" src="https://img.shields.io/badge/CI-passing-2cc653?logo=github&logoColor=white" />
    <img alt="版本 v1.3.3" src="https://img.shields.io/badge/release-v1.3.3-0e8dcc" />
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white" />
    <img alt="Node.js 24" src="https://img.shields.io/badge/Node.js-24-339933?logo=node.js&logoColor=white" />
    <img alt="FastAPI 0.115+" src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white" />
    <img alt="SvelteKit 2" src="https://img.shields.io/badge/SvelteKit-2-FF3E00?logo=svelte&logoColor=white" />
    <img alt="授權 CC BY-NC 4.0" src="https://img.shields.io/badge/License-CC_BY--NC_4.0-6f42c1" />
    <img alt="GHCR 映像" src="https://img.shields.io/badge/GHCR-gpt--image--linux-1f6f8b?logo=github&logoColor=white" />
  </p>
</div>

## 概述

GPT Image Panel 是一套輕量 Web UI，可用於圖片生成、圖片編輯、圖庫管理與本機持久化。它會連線至使用者設定的外部 GPT 相容圖片 API，並將圖片與中繼資料儲存在自己的伺服器。

本專案僅提供自架式控制面板，不提供、代理、轉售或修改任何上游模型/API 服務。實際生成能力、計費、帳號權限、內容政策及模型行為，皆由使用者設定的上游服務商決定。

## 功能

- 透過 `/v1/images/generations`、`/v1/responses` 或 OpenAI 相容的 `/v1/chat/completions` 生成圖片。
- 透過 `/v1/images/edits` 編輯圖片，可使用上傳的參考圖或 Gallery 圖片作為來源。
- API 預設管理：base URL/path/key、預設模型、response format、健康檢查、SOCKS5 Proxy、webhook 與環境變數參照式密鑰。
- 可由網頁管理的 Overall Config，顯示 env/default/override 來源，以及需要重新啟動或僅影響建置的設定標記。
- 提示詞輔助標籤、可重複使用的提示詞片段、選用的伺服器端提示詞最佳化器，以及 AI Assistant 子系統，可進行提示詞改寫/檢查/變體、參數建議、工作診斷、編輯規劃與 Gallery 圖片分析。
- SQLite 工作佇列：SSE 進度、取消、重試/沿用、持久化歷史、階段耗時資訊，以及生成/編輯共用的並行限制。
- 本機 Gallery：游標分頁、搜尋/篩選、收藏、燈箱導覽、selection token 批次操作、ZIP 匯入/匯出、縮圖、位元組大小資訊，以及非同步匯入/匯出工作。
- 選用的 Cloudflare R2 Gallery 備份同步；本機 SQLite 與圖片檔案仍是唯一真實來源。
- 存取密鑰、IP/Host 允許清單、可信任 Proxy Header、CSRF Origin 檢查、CSP nonce、版本檢查，以及選用的 JSON/Prometheus 指標。

## 架構

- 後端：`backend/app/` 下的 FastAPI；ASGI 進入點為 `backend.app.main:app`。
- 前端：`frontend/` 下的 SvelteKit 靜態應用程式；正式環境由後端提供 `frontend/build/`。
- 執行期儲存：圖片預設位於 `images/`、縮圖位於 `images/thumbs/`、SQLite 資料位於 `data/app.sqlite3`、日誌位於 `data/logs/`。
- 多 Worker 協調：排隊工作、背景 Lease、SSE Slot 與排程器所有權使用 SQLite Lease。圖片與縮圖檔案的寫入/刪除僅使用程序內鎖，並透過 UUID 檔名、原子 `Path.replace()` 與孤兒檔案 GC TTL 清理容忍跨程序競爭。
- 公開 API 路由：`backend/app/api/contract_app.py`。
- DTO：`backend/app/schemas/`。
- 持久化：`backend/app/repositories/`。
- 上游 API 整合：`backend/app/integrations/`。
- 執行期設定：`backend/app/core/settings.py`、`backend/app/core/overall_config.py`、`.env.example` 與 `docker-compose.yml`。

## 技術堆疊

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

## 專案結構

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

## 快速開始

### Docker Compose

```bash
cp .env.example .env
# 編輯 .env：至少設定 ACCESS_KEY，並視需要填入預設上游 API
# 此範例透過迴環位址使用 HTTP，需要停用 Secure cookie
ACCESS_COOKIE_SECURE=false docker-compose up -d --force-recreate
```

開啟 `http://127.0.0.1:9090`。

此本機 HTTP 範例需要設定 `ACCESS_COOKIE_SECURE=false`；透過 HTTPS 提供服務時應保持為 `true`。預設必須設定 `ACCESS_KEY`。僅在本機測試時，清空 `ACCESS_KEY` 並設定 `ALLOW_UNAUTHENTICATED=true`，這會讓所有非 health API 都不需要驗證。

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

Docker Hub 速度過慢或無法存取時：

```bash
docker build \
  --build-arg PYTHON_BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim \
  --build-arg NODE_BASE_IMAGE=docker.m.daocloud.io/library/node:24-alpine \
  -t gpt-image-panel .
```

### Caddy Reverse Proxy

Caddy 與應用程式在同一台主機執行時，請使用佔位網域，並繼續將 `9090` 連接埠僅綁定至迴環位址：

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:9090
}
```

透過 HTTPS 部署時，請在 `.env` 設定與網域相符的應用程式來源與 Host Allowlist：

```dotenv
PUBLIC_ORIGIN=https://panel.example.com
ALLOWED_HOSTS=panel.example.com
ACCESS_COOKIE_SECURE=true
```

只有一個上游時，建議使用上面的基本反代設定。如果確實需要主動健康檢查，請先確認 `/health` 在相同 `Host` Request Header 下回傳 `200`，再使用複數形式的 `health_headers` 設定區塊：

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

健康檢查回應狀態碼不在設定範圍內時，Caddy 會將上游標記為不健康。只有一個上游時，這會導致要求失敗，直到健康檢查恢復。修改後請驗證並重新載入 Caddy：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy 自動 HTTPS 要求網域解析至伺服器，且入站 `80`、`443` 連接埠可存取。使用 Cloudflare 等 CDN Proxy 時，應確認 Edge Certificate 明確涵蓋完整網域，尤其是多層子網域；否則要求尚未到達 Caddy，瀏覽器就可能顯示 `ERR_SSL_VERSION_OR_CIPHER_MISMATCH`。如果 Caddy 在容器內執行，`127.0.0.1` 指向 Caddy 容器本身，此時應改用應用程式服務名稱或其他可存取的容器網路位址。

### 常見部署問題排查

1. **容器啟動閃退並報錯 `SecretRegistryError: credentials require a non-empty startup host allowlist`**
   - **原因**：在 `.env` 中設定了 `DEFAULT_API_KEY`，但未設定 `UPSTREAM_HOST_ALLOWLIST`。
   - **解決**：在 `.env` 中將 `DEFAULT_API_URL` 對應的**純網域**填入 `UPSTREAM_HOST_ALLOWLIST`（例如 `UPSTREAM_HOST_ALLOWLIST=api.openai.com` 或 `UPSTREAM_HOST_ALLOWLIST=cf.api.fan`）。
2. **瀏覽器存取顯示 `400 Bad Request: Host is not allowed`**
   - **原因**：請求的 `Host` 標頭未在 `.env` 的 `ALLOWED_HOSTS` 或 `PUBLIC_ORIGIN` 白名單中（常見於網域名稱拼寫錯誤，或修改 `.env` 後未重新建立容器）。
   - **解決**：檢查 `.env` 中的 `PUBLIC_ORIGIN=https://panel.example.com` 與 `ALLOWED_HOSTS=panel.example.com` 拼寫是否完全一致。修改 `.env` 後必須執行 `docker compose up -d --force-recreate` 重新建立容器以套用新變數。
3. **直連 HTTP（無反代/未設定 SSL）環境下無法登入或陷入登入循環**
   - **原因**：預設 `ACCESS_COOKIE_SECURE=true` 要求必須透過 HTTPS，在純 HTTP 下瀏覽器會拒絕儲存/攜帶 Secure Cookie。
   - **解決**：純 HTTP 直連測試時，在 `.env` 中設定 `ACCESS_COOKIE_SECURE=false`；部署 HTTPS 反代後再改回 `true`。
4. **網頁端點擊「儲存預設」無法儲存、側邊欄不關閉**
   - **原因**：
     - 預設禁止直接在 Web 介面向資料庫持久化明文 API Key。若要在網頁端直接貼上明文 Key，需在 `.env` 中設定 `ALLOW_PLAINTEXT_SECRETS=true` 並重啟容器。
     - 若修改了預設中的 API URL，新網域必須同時加入 `.env` 的 `UPSTREAM_HOST_ALLOWLIST` 白名單。

### 本機開發

先使用本機 Python 3.11+ 建立專案專用虛擬環境。`.venv` 屬於本機開發狀態，專案儲存庫不提供此目錄。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements-dev.txt
npm --prefix frontend install
npm run backend:dev
```

另開一個終端機：

```bash
npm run frontend:dev
```

開啟 `http://localhost:5173`。Vite 會將 `/api` 與 `/health` 代理至 `127.0.0.1:9090`。

正式環境形式的本機 Smoke Test：

```bash
npm run frontend:build
ALLOW_UNAUTHENTICATED=true .venv/bin/granian --interface asgi backend.app.main:app --host 127.0.0.1 --port 9090
```

## 設定

大多數執行期選項都位於 `.env.example`。API 預設、提示詞最佳化器、R2 備份與部分應用程式/執行期選項，也可透過 Web Settings / Overall Config 管理。重要變數如下：

| 變數 | 用途 |
| --- | --- |
| `ACCESS_KEY` | 存取密鑰。除非清空該變數並設定 `ALLOW_UNAUTHENTICATED=true`，否則必填。 |
| `DEFAULT_API_URL` | 預設上游 API base URL，可包含或省略 `/v1`。 |
| `DEFAULT_API_KEY` | 預設上游 API key。Web Settings 建議使用 `${OPENAI_API_KEY}` 之類的 env ref。 |
| `DEFAULT_API_PATH` | `/v1/images/generations`、`/v1/responses` 或 `/v1/chat/completions`。 |
| `DEFAULT_RESPONSES_MODEL` | `/v1/responses` 在要求/預設未提供模型時使用的備援模型。 |
| `AIOHTTP_CONNECTION_LIMIT` / `AIOHTTP_CONNECTION_LIMIT_PER_HOST` | 上游要求、探測與下載共用的 aiohttp connector 限制。 |
| `APP_VERSION` / `GITHUB_REPO` / `ENABLE_VERSION_CHECK` | UI/API 版本顯示與最新 Release 檢查。 |
| `VERSION_CHECK_CACHE_SECONDS` | 每個程序成功檢查最新版本的快取有效期。 |
| `MAX_UPSTREAM_IMAGE_BYTES_PER_TASK_MB` / `UPSTREAM_MEMORY_BUDGET_MB` | 單一工作的解碼圖片上限，以及程序內上游記憶體加權准入預算。 |
| `DB_EXECUTOR_WORKERS` / `SQLITE_BUSY_*` | SQLite 專用執行器大小，以及短逾時與抖動重試控制。 |
| `IMAGE_CPU_CONCURRENCY` / `FILE_IO_CONCURRENCY` | 每個程序完整圖片解碼與阻塞式檔案 I/O 的有界並行數。 |
| `IMAGE_JOB_PROGRESS_PERSIST_INTERVAL_SECONDS` | 合併寫入圖片單元進度的最短間隔。 |
| `RUNTIME_METRICS_REFRESH_SECONDS` / `EVENT_LOOP_LAG_SAMPLE_SECONDS` | 背景協調快照與事件迴圈延遲取樣間隔。 |
| `MAX_ACTIVE_GENERATE_JOBS` | 全域執行中的生成/編輯 image unit 上限。 |
| `MAX_QUEUED_GENERATE_JOBS` | 佇列容量；超過後的新工作會回傳 `429`。 |
| `MAX_PENDING_EDIT_SOURCE_MB` | 全域待處理編輯來源圖片的位元組保留上限。 |
| `MAX_SSE_SUBSCRIBERS_GLOBAL` / `MAX_SSE_SUBSCRIBERS_PER_IP` / `SSE_CONNECTION_TTL_SECONDS` | SSE slot 限制與最長連線生命週期。 |
| `IMAGES_DIR` | 圖片儲存目錄。 |
| `THUMBNAILS_DIR` / `THUMBNAIL_*` | Gallery 縮圖儲存與產生控制。 |
| `DATA_DIR` / `DATABASE_FILE` | SQLite 執行期資料。 |
| `PROMPT_OPTIMIZER_*` | 選用的伺服器端提示詞最佳化器設定。 |
| `AI_ASSISTANT_*` | AI Assistant 預設啟用；可設 `AI_ASSISTANT_ENABLED=false` 關閉。API URL、密鑰、文字模型、逾時、路徑與 Host 允許清單沿用 `PROMPT_OPTIMIZER_*`。`AI_ASSISTANT_MAX_CONCURRENCY` 限制同時進行的上游 Assistant 呼叫，`AI_ASSISTANT_BATCH_MAX_IMAGES` 限制單次 Gallery AI 批次處理的圖片數。 |
| `R2_*` | 選用的 Cloudflare R2 Gallery 備份設定；自訂 Endpoint Host 須設定 `R2_ENDPOINT_HOST_ALLOWLIST`。 |
| `NODEIMAGE_API_KEY` | 選用的 NodeImage API key，用於從伺服器上傳 Gallery 圖片；也可在 Web Settings 中設定 env ref。 |
| `PUBLIC_ORIGIN` / `ALLOWED_HOSTS` | Reverse Proxy Host/CSRF 強化。 |
| `ENABLE_NGINX_ACCEL_REDIRECT` / `PUBLIC_IMAGE_BASE_URL` / `PUBLIC_THUMBNAIL_BASE_URL` | 選用的 nginx/CDN 圖片位元組傳送行為。 |
| `GRANIAN_*` | 正式環境程序、執行緒與靜態資源調校。 |
| `ENABLE_METRICS` | 啟用 JSON/Prometheus 指標端點。 |
| `LOG_DIR` / `LOG_LEVEL` / `LOG_RETENTION_HOURS` | 後端日誌輸出至 stdout 與輪替檔案，預設保留 24 小時。 |

Secret 欄位優先使用 `${ENV_VAR_NAME}` 參照。若要將純文字 Secret 儲存在 SQLite，必須明確設定 `ALLOW_PLAINTEXT_SECRETS=true`。

Overall Config 會將 Override 持久化至 SQLite。部分設定可熱更新；需要重新啟動或僅影響建置的設定會在 UI 中標示。為了可重現部署，仍建議透過 `.env`/Compose 管理。

## 使用方式

1. 開啟面板。
2. 若已啟用存取密鑰，先使用 `ACCESS_KEY` 解鎖。
3. 開啟 Settings。
4. 建立或選取 API 預設。
5. 設定 API base URL、API path、模型、response format 與 API key/env ref。
6. 視需要設定 SOCKS5 Proxy、webhook、提示詞最佳化器、AI Assistant、R2 備份、NodeImage 上傳或 Overall Config Override。
7. 儲存預設，必要時執行健康檢查。
8. 輸入 Prompt 生成圖片，或上傳/選取來源圖片進行編輯。
9. 在 Gallery 中沿用參數、篩選、收藏、批次操作、匯入/匯出、執行 R2 同步，或上傳至 NodeImage。

## 支援的上游路徑

| 路徑 | 說明 |
| --- | --- |
| `/v1/images/generations` | 標準圖片生成端點，從 `data[]` 讀取圖片資料。 |
| `/v1/responses` | 傳送 `prompt` 與 `model`，從 `image_generation_call` 輸出項目讀取 Base64 圖片。 |
| `/v1/chat/completions` | 傳送 OpenAI 相容的 Chat Completions 要求，從訊息或 SSE Chunk 擷取圖片 URL/Base64。 |
| `/v1/images/edits` | 供 Edits 流程使用，傳送 Multipart 來源圖片與支援的編輯參數。 |

使用 `/v1/responses` 與 `/v1/chat/completions` 時，尺寸、品質、格式、壓縮率與數量控制項會停用，因為這些路徑的參數契約不同。

## API 概覽

主要後端路由：

| 方法 | 路徑 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 健康檢查。 |
| `GET` | `/api/access/status` | 讀取存取狀態。 |
| `POST` | `/api/access` | 使用存取密鑰解鎖面板。 |
| `GET` | `/api/version`, `/api/version/latest` | 讀取目前版本與選用的最新 Release 資訊。 |
| `GET/PUT` | `/api/settings/overall-config` | 讀取/儲存 Overall Config Override。 |
| `GET/POST` | `/api/settings` | 讀取/儲存目前預設、提示詞最佳化器、R2 備份、Proxy 與 webhook 設定。 |
| `POST` | `/api/settings/presets` | 建立 API 預設。 |
| `POST` | `/api/settings/presets/{preset_id}/activate` | 啟用已儲存的 API 預設。 |
| `DELETE` | `/api/settings/presets/{preset_id}` | 刪除 API 預設。 |
| `POST` | `/api/settings/presets/{preset_id}/health` | 驗證已儲存的上游預設。 |
| `POST` | `/api/settings/r2/health` | 驗證草稿 R2 備份設定。 |
| `GET/POST` | `/api/prompt-snippets` | 列出/建立可重複使用的提示詞片段。 |
| `POST` | `/api/prompt-snippets/search` | 搜尋提示詞片段。 |
| `PATCH/DELETE` | `/api/prompt-snippets/{snippet_id}` | 更新/刪除提示詞片段。 |
| `GET/POST` | `/api/prompt/optimizer-system-prompt` | 讀取/儲存提示詞最佳化器 System Prompt。 |
| `POST` | `/api/prompt/optimize`, `/api/prompt/optimizer-health` | 最佳化提示詞或探測最佳化器連線狀態。 |
| `POST` | `/api/assistant/health` | 探測 AI Assistant 連線狀態。 |
| `POST` | `/api/assistant/prompt/rewrite`, `/api/assistant/prompt/check`, `/api/assistant/prompt/variants` | Prompt Copilot 改寫、檢查與變體工具。 |
| `POST` | `/api/assistant/generate/recommend-params` | 僅建議目前 API path 支援的生成參數。 |
| `POST` | `/api/assistant/jobs/{job_id}/diagnose`, `/api/assistant/edit/plan` | 診斷工作或規劃編輯，不會自動提交。 |
| `POST` | `/api/assistant/image/prompt` | 在記憶體中驗證並反推一張本機點陣圖的提示詞，不建立 Gallery 記錄。 |
| `POST` | `/api/assistant/image/prompt/optimize` | 結合上傳的來源圖片，最佳化反推提示詞結果。 |
| `POST/GET` | `/api/assistant/gallery/*` | 描述、反推 Prompt、分析、批次分析及讀取本機 Gallery AI 中繼資料。 |
| `POST` | `/api/generate` | 建立生成工作。 |
| `POST` | `/api/edits` | 以上傳的來源圖片建立編輯工作。 |
| `POST` | `/api/edits/from-gallery/{image_id}` | 以現有 Gallery 圖片建立編輯工作。 |
| `GET` | `/api/generate/jobs` | 列出即時工作與選用的持久化歷史。 |
| `GET` | `/api/generate/jobs/events` | 工作清單更新的 SSE 串流。 |
| `GET/DELETE` | `/api/generate/{job_id}` | 讀取或取消單一生成/編輯工作。 |
| `GET` | `/api/generate/{job_id}/events` | 單一工作 SSE。 |
| `DELETE` | `/api/generate/jobs/history` | 清除已終止的工作歷史。 |
| `GET` | `/api/gallery` | 列出/搜尋/篩選 Gallery 圖片。 |
| `POST` | `/api/gallery/search` | 使用 JSON 要求本文搜尋/篩選 Gallery。 |
| `GET/DELETE` | `/api/gallery/{image_id}` | 讀取或刪除 Gallery 圖片。 |
| `PATCH` | `/api/gallery/{image_id}/favorite` | 收藏/取消收藏單張 Gallery 圖片。 |
| `POST/PATCH` | `/api/gallery/batch/*` | Selection Token、收藏、刪除與下載等批次操作。 |
| `POST` | `/api/gallery/export-jobs`, `/api/gallery/direct-export-jobs` | 建立非同步 Gallery 匯出工作。 |
| `GET` | `/api/gallery/export-jobs/{job_id}`, `/api/gallery/direct-export-jobs/{job_id}` | 讀取非同步 Gallery 匯出工作狀態。 |
| `GET` | `/api/gallery/export-jobs/{job_id}/events`, `/api/gallery/direct-export-jobs/{job_id}/events` | Gallery 匯出工作 SSE。 |
| `GET` | `/api/gallery/export-jobs/{job_id}/download` | 下載已完成的受追蹤匯出 ZIP。 |
| `POST` | `/api/gallery/sync-jobs` | 建立 R2 備份同步工作。 |
| `GET` | `/api/gallery/sync-jobs/{job_id}`, `/api/gallery/sync-jobs/{job_id}/events` | 讀取或訂閱 R2 備份同步工作狀態。 |
| `GET` | `/api/gallery/import-jobs/{job_id}` | 讀取非同步匯入工作狀態。 |
| `GET` | `/api/gallery/import-jobs/{job_id}/events` | 非同步匯入工作狀態的 SSE 串流。 |
| `GET` | `/api/image/{filename}` | 提供通過授權的圖片位元組。 |
| `GET` | `/api/thumb/{filename}` | 提供已生成的 Gallery 縮圖。 |
| `GET` | `/api/download/{filename}` | 下載單張 Gallery 圖片。 |
| `GET` | `/api/download-all?export_job_id=` | 通過直接匯出任務串流匯出 Gallery ZIP。 |
| `POST` | `/api/import` | 匯入 Gallery ZIP；`async_job=true` 會建立匯入工作。 |
| `GET` | `/api/metrics`, `/api/metrics/prometheus` | 設定 `ENABLE_METRICS=true` 時可用。 |

公開 API 已有契約測試。除非刻意進行破壞性變更，否則請維持路徑、方法、狀態碼、SSE 事件名稱、Cookie 與回應結構穩定。

## 貢獻者界線

- 瀏覽器要求應維持同源 `/api/*`；請勿從前端直接呼叫上游模型 API、R2、webhook 目標或任意圖片 URL。
- 維持現有程式碼分層：
  - 路由與要求協調位於 `backend/app/api/routers/`
  - DTO 位於 `backend/app/schemas/`
  - 持久化與 SQLite 協調位於 `backend/app/repositories/`
  - 上游整合位於 `backend/app/integrations/`
  - 前端鏡像 API 型別位於 `frontend/src/lib/api/types.ts`
- 除非刻意進行破壞性變更，否則請維持下列公開契約：
  - API 路徑、方法、狀態碼、Cookie、SSE 事件名稱與回應結構
  - 生成/編輯佇列生命週期、取消語意與多 Worker SQLite 協調
  - 檔案系統競爭透過 UUID 檔名、原子替換與孤兒檔案 GC 來容忍；請勿依賴程序內鎖實作跨 Worker 互斥
- 集中管理驗證與安全邏輯：
  - 圖片位元組驗證、安全路徑、縮圖/封存輔助函式
  - SSRF 敏感 URL 處理應位於 Validator、Safe Connector 與整合 Client
  - 前端只能看到遮罩後的 Secret 或環境變數參照中繼資料
- 保留目前的執行期限制：
  - 編輯工作最多接受 16 張點陣來源圖片
  - Gallery ZIP 匯入/匯出沿用既有安全限制
  - SSE 使用具有全域/單一 IP 上限與 TTL 的 SQLite Slot Lease
  - R2 同步僅作備份；本機 SQLite 記錄與本機圖片檔案仍是唯一真實來源
- 新增或修改環境變數時，請同步更新 `backend/app/core/settings.py`、必要時的 `backend/app/core/overall_config.py`、`.env.example`、`docker-compose.yml` 與 README。
- 請勿提交 `images/`、`data/`、`frontend/build/`、`.svelte-kit/`、Playwright 報告、測試結果、相依套件目錄、本機資料庫或日誌等執行期/產生檔案。

## 測試

執行後端或契約測試前，請先啟用專案本機 `.venv`。npm 的契約/效能測試指令會使用 `.venv/bin/python`。

```bash
npm run frontend:check
npm run frontend:build
.venv/bin/python -m pytest backend/tests -q
npm run test:contract
npm run test:e2e
npm run test:perf
npm run test:e2e:perf
```

一般變更只需執行相關子集；大範圍變更或發行前則應執行完整測試。

若缺少 Playwright 瀏覽器：

```bash
npm --prefix frontend exec playwright install chromium
```

## 貢獻

實作界線及貢獻者應遵守的不變條件，請以上方「貢獻者界線」為準。執行與變更範圍相符的最小驗證集合；若變更行為或環境變數定義，請在同一修補中同步更新 README 與設定檔。

## 授權條款

本專案採用 `CC BY-NC 4.0`（`Creative Commons Attribution-NonCommercial 4.0 International`）授權條款。

請參閱 [LICENSE](./LICENSE)。
