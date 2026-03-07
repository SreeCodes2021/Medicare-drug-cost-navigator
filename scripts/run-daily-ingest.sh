#!/usr/bin/env bash
# Daily CMS SPUF ingest — intended for cloud schedulers (cron, EventBridge, K8s CronJob).
# Run as a separate task from the API server; shares the same DATA_DIR volume.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export DATA_DIR="${DATA_DIR:-./data}"
export DUCKDB_PATH="${DUCKDB_PATH:-${DATA_DIR}/navigator.duckdb}"

echo "[$(date -Iseconds)] Starting SPUF ingest (DATA_DIR=${DATA_DIR})"
medicare-ingest spuf --download
echo "[$(date -Iseconds)] SPUF ingest complete"
