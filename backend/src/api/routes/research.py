"""Deep research endpoint — single search bar → full lineage graph → persisted KB.

POST /api/v1/research/submit
  Body: { "query": str, "max_depth": int | None, "max_nodes": int | None }
  Returns: { run: ResearchRun, neighborhood: NeighborhoodResponse | null }

Each research run fans out across active providers (Tavily/Perplexity/DDG/
Wikipedia), extracts lineage claims with LLM+regex hybrid extraction,
recurses into discovered parents, writes the JSONL ledger, merges into the
SQLite knowledge base, and returns the neighborhood graph so the frontend
can drop it straight into the canvas.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...ingestion.research import ResearchOrchestrator
from ...ingestion.ledger import ledger_path, read_claims_by_strain
from ...graph import kb
from .neighborhood import purge_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/research", tags=["research"])


class ResearchSubmitRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=200)
    max_depth: Optional[int] = Field(default=None, ge=0, le=3)
    max_nodes: Optional[int] = Field(default=None, ge=1, le=100)


class ResearchSubmitResponse(BaseModel):
    run: Dict[str, Any]
    neighborhood: Optional[Dict[str, Any]] = None


@router.post("/submit", response_model=ResearchSubmitResponse)
async def research_submit(request: ResearchSubmitRequest):
    """Run a deep research sweep and persist to the knowledge base."""
    query = request.query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")

    orchestrator = ResearchOrchestrator(
        max_depth=request.max_depth if request.max_depth is not None else 2,
        max_nodes=request.max_nodes if request.max_nodes is not None else 30,
    )

    try:
        run = orchestrator.run(query)
    except Exception as e:
        logger.exception("research_submit failed")
        raise HTTPException(status_code=500, detail=f"research failed: {e}")

    run_dict = run.to_dict()

    # Merge into the knowledge base so it becomes a real graph citizen.
    try:
        summary = kb.merge_research_run(run_dict)
        logger.info("KB merge: %s", summary)
    except Exception as e:
        logger.exception("KB merge failed (non-fatal)")
        # Non-fatal — the run still completed; layering on top of ledger.

    # Optional source-durability sweep (CRS_WAYBACK_AUTOLOOKUP, default
    # OFF): record existing Wayback captures for up to 10 new source URLs.
    # Wrapped so it can never fail the merge or the request.
    try:
        from ...ingestion.archive import autolookup_run_sources

        autolookup_run_sources(run_dict)
    except Exception:
        logger.exception("Wayback auto-lookup failed (non-fatal)")

    # Purge the neighborhood cache so the next fetch sees the new data.
    purge_cache()

    # Return the new neighborhood for the subject so the frontend
    # can render it immediately.
    nh = None
    try:
        nh = kb.get_neighborhood(query, depth=2)
    except (KeyError, ValueError):
        pass
    resp = ResearchSubmitResponse(run=run_dict, neighborhood=nh)

    if run.error:
        # Surface the error but still return partial results.
        return resp

    return resp


@router.get("/by-strain")
async def research_by_strain(slug: str, limit: int = 20):
    """Read lineage claims previously persisted for a given strain slug."""
    target = slug.strip().lower().replace(" ", "-")
    if not target:
        raise HTTPException(status_code=400, detail="slug is required")

    claims = read_claims_by_strain(target, limit=limit)
    return {
        "slug": target,
        "claims": claims,
        "total": len(claims),
        "ledger_path": str(ledger_path()),
    }


@router.get("/runs")
async def list_research_runs(
    limit: int = Query(20, ge=1, le=100, description="Max runs (≤ 100)"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """Recent research runs from the ``research_runs`` table, newest first.

    The table is written on every KB merge; this is its read path. Each row
    carries the parsed ``providers_used`` list and the run's outcome —
    including honest failures via ``error``.
    """
    return kb.list_research_runs(limit=limit, offset=offset)


@router.get("/runs/{run_id}")
async def get_research_run(run_id: str):
    """One research run row by id.

    Deliberately returns the row alone: research.py has no reusable
    ledger-read helper (by-strain reads inline, filtered per slug), and no
    new ledger plumbing is built for per-run claim replay.
    """
    run = kb.get_research_run(run_id)
    if not run:
        raise HTTPException(
            status_code=404, detail=f"Research run '{run_id}' not found"
        )
    return run
