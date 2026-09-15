"""Disposable provider/wiki cache — TTL + content-hash, not evidence.

Re-research of the same strain should not re-hit Wikipedia or billed
search providers. This store is a network skip: the ingest ledger stays
append-only, ``data/raw/<run_id>/`` still records what a run saw, and
this file can be deleted at any time without touching the KB.

Write boundary: never writes strains, lineage_sources, claims, or the
ledger. Keys resolve through ``kb.canonical_identity`` so ``GSC`` and
``Girl Scout Cookies`` share one blob once an alias exists.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Same data root as kb._DEFAULT_KB (backend/data/). This module lives one
# directory deeper (ingestion/research/) than graph/kb.py, so parents[3]
# not parents[2] — parents[2] would land inside the src package.
_DEFAULT_CACHE = Path(__file__).resolve().parents[3] / "data" / "provider_cache.db"
_DEFAULT_TTL = 7 * 24 * 3600  # 7 days
_COUNTERS = frozenset({"hits", "misses", "stores"})
_ZERO_COUNTERS = {"hits": 0, "misses": 0, "stores": 0, "entries": 0}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS provider_cache (
    cache_key    TEXT PRIMARY KEY,
    provider     TEXT NOT NULL,
    kind         TEXT NOT NULL,
    identity     TEXT NOT NULL,
    payload      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    fetched_at   INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    hits         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_cache_expires ON provider_cache (expires_at);
CREATE TABLE IF NOT EXISTS cache_counters (
    id     INTEGER PRIMARY KEY CHECK (id = 1),
    hits   INTEGER NOT NULL DEFAULT 0,
    misses INTEGER NOT NULL DEFAULT 0,
    stores INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO cache_counters (id, hits, misses, stores)
VALUES (1, 0, 0, 0);
"""


def cache_path() -> Path:
    """Resolve the cache file. Read per-call so tests can override."""
    return Path(os.environ.get("CRS_PROVIDER_CACHE_PATH", str(_DEFAULT_CACHE)))


def ttl_seconds() -> int:
    raw = os.environ.get("CRS_PROVIDER_CACHE_TTL_SECONDS", str(_DEFAULT_TTL))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_TTL


def _identity(query: str) -> str:
    try:
        from ...graph.kb import canonical_identity

        ident = canonical_identity(query)
        if ident:
            return ident
    except Exception:
        logger.debug("canonical_identity failed for %r", query, exc_info=True)
    from ...graph.kb import normalize_slug

    return normalize_slug(query) or (query or "").strip().lower()


def _make_key(provider: str, kind: str, identity: str, extra: str = "") -> str:
    return f"{provider}:{kind}:{identity}:{extra or ''}"


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def content_hash(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _worth_storing(payload: Any) -> bool:
    if payload is None:
        return False
    if isinstance(payload, (list, dict, str)) and len(payload) == 0:
        return False
    return True


@contextmanager
def connect():
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _bump(conn: sqlite3.Connection, column: str, n: int = 1) -> None:
    if column not in _COUNTERS:
        raise ValueError(f"unknown cache counter {column!r}")
    conn.execute(
        f"UPDATE cache_counters SET {column} = {column} + ? WHERE id = 1",
        (n,),
    )


def _payload_digest(blob: str) -> str:
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _safe_bump_miss() -> None:
    try:
        with connect() as conn:
            _bump(conn, "misses")
    except Exception:
        logger.debug("provider cache miss-counter unavailable", exc_info=True)


def cached_payload(
    *,
    provider: str,
    kind: str,
    query: str,
    extra: str = "",
    fetch: Callable[[], Any],
) -> Any:
    """Return a cached JSON payload or call ``fetch`` and store it.

    TTL of 0 disables the cache (always fetch, never store). Empty/None
    fetch results are returned but not stored — a network failure must
    not poison the next run. Cache IO errors degrade to a live fetch;
    this store is disposable and must never take research down.
    """
    identity = _identity(query)
    key = _make_key(provider, kind, identity, extra)
    ttl = ttl_seconds()
    if ttl <= 0:
        _safe_bump_miss()
        return fetch()

    now = int(time.time())
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT payload, content_hash, expires_at "
                "FROM provider_cache WHERE cache_key=?",
                (key,),
            ).fetchone()
            if row is not None:
                if int(row["expires_at"]) > now:
                    stored = row["payload"]
                    digest = row["content_hash"] or ""
                    if _payload_digest(stored) == digest:
                        try:
                            payload = json.loads(stored)
                        except json.JSONDecodeError:
                            conn.execute(
                                "DELETE FROM provider_cache WHERE cache_key=?",
                                (key,),
                            )
                        else:
                            conn.execute(
                                "UPDATE provider_cache SET hits = hits + 1 "
                                "WHERE cache_key=?",
                                (key,),
                            )
                            _bump(conn, "hits")
                            return payload
                    conn.execute(
                        "DELETE FROM provider_cache WHERE cache_key=?", (key,)
                    )
                else:
                    conn.execute(
                        "DELETE FROM provider_cache WHERE cache_key=?", (key,)
                    )
            _bump(conn, "misses")
    except Exception:
        logger.debug(
            "provider cache lookup failed; fetching live", exc_info=True
        )
        return fetch()

    payload = fetch()
    if _worth_storing(payload):
        try:
            blob = _canonical_json(payload)
            digest = _payload_digest(blob)
            now = int(time.time())
            with connect() as conn:
                conn.execute(
                    """
                    INSERT INTO provider_cache
                        (cache_key, provider, kind, identity, payload, content_hash,
                         fetched_at, expires_at, hits)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        payload = excluded.payload,
                        content_hash = excluded.content_hash,
                        fetched_at = excluded.fetched_at,
                        expires_at = excluded.expires_at
                    """,
                    (key, provider, kind, identity, blob, digest, now, now + ttl),
                )
                _bump(conn, "stores")
        except Exception:
            logger.debug("provider cache store failed", exc_info=True)
    return payload


def counters() -> Dict[str, int]:
    """Hit/miss/store totals. Never raises — cache is disposable."""
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT hits, misses, stores FROM cache_counters WHERE id=1"
            ).fetchone()
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM provider_cache"
            ).fetchone()["c"]
        if row is None:
            return dict(_ZERO_COUNTERS)
        return {
            "hits": int(row["hits"] or 0),
            "misses": int(row["misses"] or 0),
            "stores": int(row["stores"] or 0),
            "entries": int(n),
        }
    except Exception:
        logger.debug("provider cache counters unavailable", exc_info=True)
        return dict(_ZERO_COUNTERS)


def cache_stats() -> Dict[str, Any]:
    c = counters()
    attempted = c["hits"] + c["misses"]
    return {
        **c,
        "hit_rate": (round(c["hits"] / attempted, 4) if attempted else None),
        "ttl_seconds": ttl_seconds(),
    }


def purge() -> int:
    """Drop every cached blob. Counters stay (lifetime accounting)."""
    with connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS c FROM provider_cache").fetchone()["c"]
        conn.execute("DELETE FROM provider_cache")
    return int(n)


def peek(
    provider: str, kind: str, query: str, extra: str = ""
) -> Optional[Dict[str, Any]]:
    """Test helper: raw row for a key, or None."""
    identity = _identity(query)
    key = _make_key(provider, kind, identity, extra)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM provider_cache WHERE cache_key=?", (key,)
        ).fetchone()
    return dict(row) if row is not None else None
