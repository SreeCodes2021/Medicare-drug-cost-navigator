#!/usr/bin/env bash
# Copy static frontend sources into frontend/dist/ for local dev and tests.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/frontend/src"
DIST="$ROOT/frontend/dist"

if [[ ! -f "$SRC/index.html" ]]; then
  echo "Missing frontend source at $SRC" >&2
  exit 1
fi

mkdir -p "$DIST"
cp "$SRC/index.html" "$SRC/app.js" "$SRC/styles.css" "$DIST/"
