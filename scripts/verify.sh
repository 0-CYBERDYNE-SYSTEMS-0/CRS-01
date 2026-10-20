#!/usr/bin/env bash
# One-command verification for CRS-01. Exit 0 means every required check passed.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0

step() { printf "\033[1;34m== %s\033[0m\n" "$1"; }
run() {
  local label="$1"
  shift
  printf "\033[1;36mrun: %s\033[0m\n" "$label"
  "$@"
  local status=$?
  if [ "$status" -ne 0 ]; then
    printf "\033[1;31mFAIL %s (exit %s)\033[0m\n" "$label" "$status"
    fail=1
  else
    printf "\033[1;32mok: %s\033[0m\n" "$label"
  fi
  return 0
}

step "git sanity"
run "git fsck" git fsck --no-progress
if status=$(git status --porcelain 2>/dev/null); then
  if [ -z "$status" ]; then
    echo "ok: clean tree"
  else
    echo "warn: dirty tree (not a failure)"
  fi
else
  echo "FAIL git status"
  fail=1
fi

if [ -f frontend/package.json ]; then
  step "frontend"
  if command -v npm >/dev/null 2>&1; then
    run "npm ci (frontend)" bash -c 'cd frontend && npm ci --prefer-offline --no-audit --no-fund'
    run "npm run lint (frontend)" bash -c 'cd frontend && npm run lint'
    run "npm run build (frontend)" bash -c 'cd frontend && npm run build'
  else
    echo "FAIL npm is required for the frontend checks"
    fail=1
  fi
fi

if [ -f backend/pyproject.toml ] || [ -f backend/requirements.txt ]; then
  step "backend"
  backend_python="backend/.venv/bin/python"
  if [ ! -x "$backend_python" ]; then
    backend_python="$(command -v python3 || true)"
  fi
  if [ -z "$backend_python" ]; then
    echo "FAIL python3 is required for backend checks"
    fail=1
  elif ! "$backend_python" -c 'import pytest' >/dev/null 2>&1; then
    echo "FAIL pytest is required for backend checks (install backend[dev])"
    fail=1
  else
    run "pytest backend/tests" "$backend_python" -m pytest -q backend/tests
  fi
fi

exit "$fail"
