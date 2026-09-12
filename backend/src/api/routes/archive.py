from __future__ import annotations
"""Archive endpoints — real Wayback Machine availability, persisted.

Replaces the Phase-1 in-memory stub. What is true here:

- POST /archive/lookup   — asks archive.org whether a capture of the URL
  already exists (4s timeout, fail-open) and, on a hit, records it on the
  sources row. A miss is reported as ``archived: false`` — never hidden.
- POST /archive/ingest   — thin alias of lookup (same body) kept so any
  existing callers keep working; it no longer fabricates a ``source_id``.
- GET  /archive/sources  — the real sources table read (newest fetched
  first), with each row's capture state. NULL ``wayback_url`` means
  exactly "no capture on record".
- POST /archive/wayback  — deliberately gone: CRS-01 reads existing
  captures, it does not submit pages for archiving, and the old stub
  pretended to. The path now answers 501 with that honesty.

The Wayback lookups themselves live in backend/src/ingestion/archive.py
(``wayback_availability``), so agents and the research merge can reuse
them without going through the API layer.
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...graph import kb
from ...ingestion.archive import wayback_availability

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/archive", tags=["archive"])


class LookupRequest(BaseModel):
    url: str = Field(..., min_length=1)
    # Accepted for backwards compatibility with the old ingest body; it
    # does not influence the lookup.
    strain_hint: Optional[str] = None


def _lookup(url: str) -> dict:
    url = (url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    result = wayback_availability(url)
    response: dict = {"url": url, "archived": bool(result.get("archived"))}
    if result.get("archived"):
        # Persist the hit so evidence rows and claims can link to the
        # archived copy. set_source_archive never fabricates a capture —
        # everything below came from archive.org's answer.
        try:
            kb.set_source_archive(
                url, result.get("wayback_url"), result.get("archived_at")
            )
        except Exception as exc:  # persistence must not mask the answer
            logger.warning("could not persist wayback hit for %s: %s", url, exc)
        response["wayback_url"] = result.get("wayback_url")
        response["archived_at"] = result.get("archived_at")
        if result.get("available_status"):
            response["available_status"] = result.get("available_status")
    return response


@router.post("/lookup")
async def lookup_archive(request: LookupRequest):
    """Check archive.org for an existing capture of ``url``, record a hit.

    Fail-open: when archive.org is unreachable or answers oddly the answer
    is honestly ``{archived: false}`` — a failed lookup is "not archived",
    never an error to hide.
    """
    return _lookup(request.url)


@router.post("/ingest")
async def ingest_url(request: LookupRequest):
    """Backwards-compatible alias of POST /archive/lookup.

    The old stub returned a fabricated ``source_id``; the new truth is the
    lookup result itself. ``strain_hint`` is accepted and ignored.
    """
    return _lookup(request.url)


@router.post("/wayback")
async def submit_to_wayback(url: str = ""):
    """Not implemented — on purpose.

    CRS-01 records captures that already exist; it does not push pages to
    the Wayback Machine. The previous stub answered as if a submission had
    been queued, which was neither true nor archived anywhere.
    """
    raise HTTPException(
        status_code=501,
        detail=(
            "Wayback submission is not implemented: CRS-01 only reads "
            "existing captures (POST /archive/lookup) and never claims to "
            "have archived a page it didn't observe."
        ),
    )


@router.get("/sources")
async def list_archived_sources(
    limit: int = Query(50, ge=1, le=200, description="Max sources (≤ 200)"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """Sources table read: url, title, engine, fetched_at and the recorded
    Wayback capture (when one exists), newest fetched first."""
    return kb.list_sources(limit=limit, offset=offset)
