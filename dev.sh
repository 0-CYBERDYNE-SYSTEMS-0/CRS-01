#!/usr/bin/env bash
#
# Run the full CRS-01 stack — FastAPI backend + Next.js frontend — from ONE
# terminal. Press Ctrl-C to stop both.
#
# Usage:  ./dev.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UVICORN="$ROOT/backend/.venv/bin/uvicorn"
NEXT="$ROOT/frontend/node_modules/.bin/next"

cd "$ROOT"

# ── Prerequisites ──────────────────────────────────────────────────────────
if [ ! -x "$UVICORN" ]; then
  echo "✗ Backend venv missing at backend/.venv"
  echo "  Setup:  python3 -m venv backend/.venv && backend/.venv/bin/pip install -e 'backend[dev]'"
  exit 1
fi
if [ ! -x "$NEXT" ]; then
  echo "✗ Frontend dependencies missing — run: (cd frontend && npm install)"
  exit 1
fi

PIDS=()
cleanup() {
  echo
  echo "▶ Stopping backend + frontend…"
  for p in "${PIDS[@]:-}"; do
    kill "$p" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "▶ Backend  → http://localhost:8000  (Ctrl-C stops both)"
( cd "$ROOT/backend" && exec "$UVICORN" src.main:app --reload --port 8000 ) &
PIDS+=($!)

echo "▶ Frontend → http://localhost:3000"
( cd "$ROOT/frontend" && exec "$NEXT" dev ) &
PIDS+=($!)

wait