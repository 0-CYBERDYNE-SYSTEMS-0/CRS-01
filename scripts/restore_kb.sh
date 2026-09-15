#!/usr/bin/env bash
# Restore a snapshot tarball into backend/data/.
# Usage: ./scripts/restore_kb.sh backend/data/snapshots/crs01-<label>-<ts>.tar.gz
#
# Overwrites crs01.db, ingest_ledger.jsonl, and raw/ from the archive.
# Never revives provider_cache.db — that file is a disposable network skip.

set -euo pipefail
cd "$(dirname "$0")/.."

ARCHIVE="${1:-}"
if [ -z "$ARCHIVE" ] || [ ! -f "$ARCHIVE" ]; then
  echo "Usage: $0 <snapshot.tar.gz>" >&2
  exit 1
fi

# File members (crs01.db, ingest_ledger.jsonl) are replaced wholesale by
# tar; directories merge. Drop live raw/ first so post-snapshot evidence
# cannot survive as a mixed-timeline directory.
if [ -d backend/data/raw ]; then
  rm -rf backend/data/raw
fi

mkdir -p backend/data
tar -xzf "$ARCHIVE" -C backend/data

# A snapshot taken before this rule, or a hand-rolled tarball, might still
# contain a cache file. Drop it so restore cannot masquerade cache as KB.
rm -f backend/data/provider_cache.db \
      backend/data/provider_cache.db-wal \
      backend/data/provider_cache.db-shm

echo "Restored $ARCHIVE into backend/data/"
echo "provider_cache.db left disposable (not restored)"
