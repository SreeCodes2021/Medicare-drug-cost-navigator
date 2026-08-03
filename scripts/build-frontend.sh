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

mkdir -p "$DIST/icons"
cp "$SRC/index.html" "$SRC/app.js" "$SRC/styles.css" "$SRC/manifest.json" "$DIST/"
cp "$SRC"/icons/*.png "$DIST/icons/"

echo "Built frontend → $DIST"
echo "  index.html  app.js  styles.css  manifest.json  icons/"
echo ""
echo "Serve locally (pick a free port if 8000 is taken):"
echo "  uvicorn medicare_navigator.api.app:app --reload --host 0.0.0.0 --port 8001"
echo "  open http://localhost:8001"
