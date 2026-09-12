"""Neighborhood endpoint for a single strain — the subgraph the frontend
GraphCanvas renders when a tester clicks a strain.

Backed by the SQLite knowledge base (backend/src/graph/kb.py). Every strain
in the KB got there via a live research run (or human curation later) —
there is no mock data on this path.

Design notes:
- We return ReactFlow-shaped nodes/edges so the frontend can drop them in
  without a transform. Node `type` uses the capitalized forms the existing
  GraphCanvas registers ("Strain", "Person", "Claim") and `data.trust_tier`
  / `data.confidence` / `data.relation` / `data.origin` mirror the CRS-01
  schema. Lineage edges carry agreement colors (green/amber/red).
- An in-process TTL cache keyed by (slug, depth). Cache-Control + ETag
  headers let the frontend skip the network on repeat calls. The cache is
  purged whenever a research run merges new evidence into the KB.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query, Response, Header

from ...graph import kb

router = APIRouter(prefix="/graph", tags=["graph"])


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class _CacheEntry:
    __slots__ = ("payload", "expires_at", "etag")

    def __init__(self, payload: Dict[str, Any], expires_at: float, etag: str) -> None:
        self.payload = payload
        self.expires_at = expires_at
        self.etag = etag


_CACHE: Dict[Tuple[str, int], _CacheEntry] = {}
_TTL_SECONDS = 60


def purge_cache() -> int:
    """Drop cached neighborhoods (called after a research merge)."""
    cleared = len(_CACHE)
    _CACHE.clear()
    return cleared


def _cache_get(key: Tuple[str, int]) -> Optional[_CacheEntry]:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    if entry.expires_at < time.time():
        _CACHE.pop(key, None)
        return None
    return entry


def _cache_set(key: Tuple[str, int], payload: Dict[str, Any]) -> _CacheEntry:
    raw = repr(_stable_items(payload)).encode("utf-8")
    etag = 'W/"' + hashlib.sha1(raw).hexdigest()[:16] + '"'
    entry = _CacheEntry(payload=payload, expires_at=time.time() + _TTL_SECONDS, etag=etag)
    _CACHE[key] = entry
    return entry


def _stable_items(obj: Any) -> List[Tuple[str, Any]]:
    """Sort dicts so the etag is stable regardless of insertion order."""
    if isinstance(obj, dict):
        return [(k, _stable_items(v)) for k, v in sorted(obj.items())]
    if isinstance(obj, list):
        return [("list", _stable_items(v)) for v in obj]
    return [("v", obj)]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/strains/{slug}/neighborhood")
async def get_neighborhood(
    slug: str,
    response: Response,
    depth: int = Query(2, ge=0, le=3, description="Hops from the seed strain (0..3)"),
    if_none_match: Optional[str] = Header(default=None, alias="If-None-Match"),
):
    """Return the k-hop subgraph around a strain from the knowledge base."""
    key = (slug.lower().strip().replace(" ", "-"), depth)
    entry = _cache_get(key)
    if entry is not None:
        if if_none_match and if_none_match == entry.etag:
            response.headers["ETag"] = entry.etag
            response.headers["X-Cache"] = "HIT-304"
            return Response(
                status_code=304,
                headers={"ETag": entry.etag, "X-Cache": "HIT-304"},
            )
        response.headers["ETag"] = entry.etag
        response.headers["Cache-Control"] = f"private, max-age={_TTL_SECONDS}"
        response.headers["X-Cache"] = "HIT"
        return entry.payload

    try:
        payload = kb.get_neighborhood(slug, depth)
    except KeyError:
        suggestions = kb.suggest_similar(slug, limit=4)
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Strain '{slug}' not found in the knowledge base",
                "suggestions": suggestions,
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    entry = _cache_set(key, payload)
    response.headers["ETag"] = entry.etag
    response.headers["Cache-Control"] = f"private, max-age={_TTL_SECONDS}"
    response.headers["X-Cache"] = "MISS"
    return payload


@router.post("/cache/purge")
async def purge_cache_endpoint():
    """Test/admin endpoint: clear the in-process neighborhood cache."""
    return {"cleared_entries": purge_cache()}
