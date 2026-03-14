#!/usr/bin/env bash
# Start supercronic (nightly SPUF ingest) and uvicorn for Render / Docker.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export DATA_DIR="${DATA_DIR:-/data}"
export DUCKDB_PATH="${DUCKDB_PATH:-${DATA_DIR}/navigator.duckdb}"
export CHROMA_PATH="${CHROMA_PATH:-${DATA_DIR}/chroma}"
mkdir -p "$DATA_DIR" "$CHROMA_PATH" "$(dirname "$DUCKDB_PATH")"

CRONTAB_FILE="$(mktemp)"
python "$ROOT/scripts/generate-crontab.py" >"$CRONTAB_FILE"
supercronic "$CRONTAB_FILE" &

PORT="${PORT:-${API_PORT:-8000}}"
exec uvicorn medicare_navigator.api.app:app --host 0.0.0.0 --port "$PORT"
