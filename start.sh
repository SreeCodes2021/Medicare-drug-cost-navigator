#!/usr/bin/env bash
# Start the Medicare Navigator API and UI (no data ingestion).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -z "${VIRTUAL_ENV:-}" && -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck source=/dev/null
  source "$ROOT/.venv/bin/activate"
fi

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ROOT/.env"
  set +a
fi

export DATA_DIR="${DATA_DIR:-./data}"
export DUCKDB_PATH="${DUCKDB_PATH:-${DATA_DIR}/navigator.duckdb}"
mkdir -p "$DATA_DIR" "$(dirname "$DUCKDB_PATH")"

"$ROOT/scripts/build-frontend.sh"

HOST="${API_HOST:-0.0.0.0}"
PORT="${PORT:-${API_PORT:-8000}}"

echo ""
echo "Starting server at http://localhost:${PORT}"
echo ""

exec uvicorn medicare_navigator.api.app:app --reload --host "$HOST" --port "$PORT"
