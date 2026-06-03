FROM alpine:3.20 AS frontend-builder

WORKDIR /build
COPY frontend/src/ ./src/
RUN mkdir -p dist && cp -a src/. dist/

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /usr/local/bin/supercronic \
        "https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-amd64" \
    && chmod +x /usr/local/bin/supercronic \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY scripts ./scripts
COPY --from=frontend-builder /build/dist ./frontend/dist

RUN pip install --no-cache-dir .

RUN chmod +x scripts/docker-start.sh scripts/run-daily-ingest.sh

ENV PROJECT_ROOT=/app \
    DATA_DIR=/data \
    DUCKDB_PATH=/data/navigator.duckdb \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["/app/scripts/docker-start.sh"]
