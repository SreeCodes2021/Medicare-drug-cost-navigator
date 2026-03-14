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
COPY frontend/dist ./frontend/dist
COPY scripts ./scripts

RUN pip install --no-cache-dir .

RUN chmod +x scripts/docker-start.sh scripts/run-daily-ingest.sh

ENV DATA_DIR=/data \
    DUCKDB_PATH=/data/navigator.duckdb \
    CHROMA_PATH=/data/chroma \
    PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["/app/scripts/docker-start.sh"]
