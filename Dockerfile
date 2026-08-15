ARG PYTHON_BASE_IMAGE=python:3.11-slim@sha256:a630a63cdb314e2d138a2fca3e375e319e8568346ffafac5b980f888630ac4f1
ARG NODE_BASE_IMAGE=node:24-alpine@sha256:d32cdf619f63fe0471182d08996dd516c6275bb5fd31ae06e55a570bd9e1ad43
ARG NGINX_BASE_IMAGE=nginx:alpine@sha256:4a73073bd557c65b759505da037898b61f1be6cbcc3c2c3aeac22d2a470c1752

FROM ${NODE_BASE_IMAGE} AS frontend-builder
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npx svelte-kit sync && npm run build

FROM ${PYTHON_BASE_IMAGE} AS python-builder
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --require-hashes --prefix=/install -r requirements.txt

FROM ${NGINX_BASE_IMAGE} AS nginx
COPY --from=frontend-builder /frontend/build /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf

FROM ${PYTHON_BASE_IMAGE} AS runtime

LABEL org.opencontainers.image.source="https://github.com/Z1rconium/gpt-image-linux"

RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g appgroup -s /bin/bash -m appuser

WORKDIR /app
RUN mkdir images data && \
    chown -R appuser:appgroup images data
COPY --from=python-builder --chown=appuser:appgroup /install /usr/local
COPY --chown=appuser:appgroup VERSION .
COPY --chown=appuser:appgroup backend/ ./backend/
COPY --from=frontend-builder --chown=appuser:appgroup /frontend/build ./frontend/build

EXPOSE 9090

ENV GRANIAN_INTERFACE=asgi \
    GRANIAN_HOST=0.0.0.0 \
    GRANIAN_PORT=9090 \
    GRANIAN_LOOP=uvloop \
    GRANIAN_RUNTIME_THREADS=2 \
    GRANIAN_RUNTIME_MODE=auto \
    GRANIAN_WORKERS=1 \
    GRANIAN_BACKPRESSURE=100 \
    GRANIAN_BACKLOG=2048 \
    GRANIAN_STATIC_PATH_ROUTE=/_app/immutable \
    GRANIAN_STATIC_PATH_MOUNT=/app/frontend/build/_app/immutable \
    GRANIAN_STATIC_PATH_EXPIRES=31536000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9090/health')" || exit 1

USER appuser

CMD ["granian", "backend.app.main:app"]
