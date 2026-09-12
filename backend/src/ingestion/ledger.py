"""The one ingest-ledger path and reader.

The JSONL ledger (``backend/data/ingest_ledger.jsonl`` by default, override
with ``CRS_INGEST_LEDGER``) is the append-only audit trace of every research
run. This module is the single place that resolves its location and the
single read helper over it — consumers must not re-implement the path
derivation or the claim-matching logic inline.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

_DEFAULT_LEDGER = Path(__file__).resolve().parents[2] / "data" / "ingest_ledger.jsonl"


def ledger_path() -> Path:
    """Resolve the ledger location, read per-call so tests can override via
    ``CRS_INGEST_LEDGER``. The parent directory is created so first appends
    (and the raw-evidence dumps that live beside the ledger) have somewhere
    to land. The ledger file itself is only ever created by appends."""
    path = Path(os.environ.get("CRS_INGEST_LEDGER", str(_DEFAULT_LEDGER)))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def read_claims_by_strain(slug: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Lineage-claim rows from the ledger that mention ``slug``, newest first.

    A row matches when its child, its source query, or either parent slug
    equals the target (each compared after the same lowercase/space→hyphen
    fold the writers used). Missing ledger yields [] — honest absence, never
    an error. Never writes.
    """
    target = slug.strip().lower().replace(" ", "-")
    ledger = ledger_path()
    if not target or not ledger.exists():
        return []

    matches: List[Dict[str, Any]] = []
    with ledger.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("kind") != "lineage_claim":
                continue
            claim = row.get("claim", {}) or {}
            child = (claim.get("child") or "").strip().lower().replace(" ", "-")
            q = (row.get("query") or "").strip().lower().replace(" ", "-")
            if target in (child, q) or target in (
                (claim.get("parent_a") or "").lower().replace(" ", "-"),
                (claim.get("parent_b") or "").lower().replace(" ", "-"),
            ):
                matches.append(row)

    matches.sort(key=lambda r: r.get("ingested_at", 0), reverse=True)
    return matches[:limit]
