#!/usr/bin/env bash
# Snapshot the local KB + ledger into a portable tarball.
# Usage:  ./scripts/snapshot_kb.sh [label]
# Output: backend/data/snapshots/crs01-<label>-<timestamp>.tar.gz
# Restore: ./scripts/restore_kb.sh <that-tarball>
#
# Packs crs01.db (the graph), ingest_ledger.jsonl, and raw/. Does NOT pack
# provider_cache.db — that file is a disposable network skip, not evidence.

set -euo pipefail
cd "$(dirname "$0")/.."

LABEL="${1:-manual}"
TS="$(date +%Y%m%d-%H%M%S)"
OUT="backend/data/snapshots/crs01-${LABEL}-${TS}.tar.gz"

mkdir -p backend/data/snapshots

# Collect real files (only ones that exist), then tar them from the data/ root.
ARGS=()
[ -f backend/data/crs01.db ] && ARGS+=(crs01.db)
[ -f backend/data/ingest_ledger.jsonl ] && ARGS+=(ingest_ledger.jsonl)
[ -d backend/data/raw ]       && ARGS+=(raw)

if [ ${#ARGS[@]} -eq 0 ]; then
  echo "No KB or ledger data found under backend/data/. Nothing to snapshot."
  exit 0
fi

tar -czf "$OUT" \
  --exclude='__pycache__' \
  --exclude='provider_cache.db' \
  --exclude='provider_cache.db-wal' \
  --exclude='provider_cache.db-shm' \
  -C backend/data \
  "${ARGS[@]}"

echo "Wrote $OUT"
echo "Size: $(du -h "$OUT" | cut -f1)"
echo "Restore: ./scripts/restore_kb.sh $OUT"

MANIFEST="${OUT%.tar.gz}.manifest.txt"
{
  echo "CRS-01 KB snapshot"
  echo "Created: $(date -Iseconds)"
  echo "Label:   $LABEL"
  echo
  echo "Knowledge base:"
  if [ -f backend/data/crs01.db ]; then
    du -h backend/data/crs01.db | awk '{printf "  crs01.db %s\n", $1}'
  else
    echo "  (no crs01.db)"
  fi
  echo
  echo "Ledger entries:"
  if [ -f backend/data/ingest_ledger.jsonl ]; then wc -l backend/data/ingest_ledger.jsonl | awk '{printf "  %s lines\n", $1}'; else echo "  (no ledger)"; fi
  echo
  echo "Raw provider responses:"
  if [ -d backend/data/raw ]; then find backend/data/raw -type f 2>/dev/null | wc -l | xargs printf "  %s files\n"; else echo "  (none)"; fi
  echo
  echo "Not included: provider_cache.db (disposable)"
} > "$MANIFEST"

echo "Manifest: $MANIFEST"
