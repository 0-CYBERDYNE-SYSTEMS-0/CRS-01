from __future__ import annotations
"""Graph retrieval endpoints for CRS-01 API.

Backed by the SQLite knowledge base (backend/src/graph/kb.py). Every strain
listed here was discovered by a live research run — the mock dataset was
removed 2026-08-19 ("no mock nothing").

Route registration order matters: `/strains` and `/strains/search` MUST be
registered BEFORE `/strains/{name}`, otherwise FastAPI's path matcher
captures the literal string "search" as a `{name}` parameter and the
search endpoint silently 404s.
"""
from fastapi import APIRouter, HTTPException, Query

from ...graph import kb

router = APIRouter(prefix="/graph", tags=["graph"])


# ---------------------------------------------------------------------------
# Routes — registered in this order so `/strains` and `/strains/search` are
# matched before `/strains/{name}` swallows them as a path parameter.
# ---------------------------------------------------------------------------


@router.get("/strains")
async def list_strains(
    limit: int = Query(50, ge=1, le=200, description="Max strains to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """List strains in the knowledge base. Used by the frontend to populate
    the 'recently researched' suggestion chips under the hero copy."""
    return kb.list_strains(limit=limit, offset=offset)


@router.get("/strains/search")
async def search_strains(
    q: str = Query(..., min_length=1, max_length=64, description="Substring match on name/slug"),
    limit: int = Query(8, ge=1, le=20, description="Max matches"),
):
    """Substring search for strain autocomplete. Returns the best matches
    by case-insensitive substring on name, slug, or display label."""
    return kb.search_strains(q, limit=limit)


@router.get("/strains/{name}")
async def get_strain(name: str):
    """Get a strain by name. Registered AFTER /strains and /strains/search
    so the literal routes win."""
    strain = kb.get_strain(name)
    if not strain:
        suggestions = kb.suggest_similar(name, limit=4)
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Strain '{name}' not found in the knowledge base",
                "suggestions": suggestions,
            },
        )
    return strain


@router.get("/stats")
async def get_graph_stats():
    """Get knowledge-base statistics, including LLM spend and cache counters."""
    payload = kb.stats()
    try:
        from ...ingestion.research.provider_cache import cache_stats

        payload["provider_cache"] = cache_stats()
    except Exception:
        payload["provider_cache"] = {
            "hits": 0,
            "misses": 0,
            "stores": 0,
            "entries": 0,
            "hit_rate": None,
            "ttl_seconds": 0,
        }
    return payload
