"""Human curation endpoints — the only path to VERIFIED.

The locked evidence model (2026-08-19) reserves VERIFIED for human
curation and never lets the pipeline assign it. These routes are that
human loop:

- POST /graph/strains/{slug}/review     — set/clear a human trust tier
- POST /graph/observations/quarantine   — flag a bad raw observation out
                                          of every aggregate

Both are plain-dict responses (no response models), matching the
neighboring per-strain routes in graph.py / evidence.py. CONTRADICTED is
deliberately not assignable here: it is a data-derived verdict (conflicting
parent sets), not a curation choice — it arrives and leaves with the
evidence.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...graph import kb
from .evidence import _require_strain

router = APIRouter(prefix="/graph", tags=["curation"])


class ReviewRequest(BaseModel):
    tier: Optional[str] = None
    note: str = Field(default="", max_length=2000)


class QuarantineRequest(BaseModel):
    child_slug: str
    parent_slug: str
    source_url: str
    quarantined: bool = True


@router.post("/strains/{slug}/review")
async def review_strain(slug: str, request: ReviewRequest):
    """Set (tier) or clear (tier=null) a human trust-tier verdict.

    VERIFIED is meaningful precisely because only this route can grant it;
    the curated stamp (origin='curated', note, timestamp) stays visible so
    the tier is always attributable to a person, not to consensus math.
    """
    # Existence first, so an unknown strain is always 404 regardless of
    # what tier came in the body.
    _require_strain(slug)

    tier = request.tier
    if tier is not None:
        tier = tier.strip().upper()
        if tier == "CONTRADICTED":
            raise HTTPException(
                status_code=400,
                detail=(
                    "CONTRADICTED is a data-derived verdict (conflicting "
                    "parent sets) and cannot be assigned by human curation. "
                    "Quarantine the bad observations instead."
                ),
            )
        if tier not in kb.CURATABLE_TIERS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unknown tier {request.tier!r}. Assignable tiers: "
                    f"{', '.join(kb.CURATABLE_TIERS)}, or null to clear."
                ),
            )

    kb.set_curated_tier(slug, tier, request.note or "")
    return kb.get_strain(slug)


@router.post("/observations/quarantine")
async def quarantine_observation(request: QuarantineRequest):
    """Quarantine (or restore) one raw observation by its full identity
    (child, parent, source_url). The row stays on file — quarantine is a
    flag, not a deletion — but every aggregate re-derives without it."""
    if not request.child_slug.strip() or not request.parent_slug.strip() or not request.source_url.strip():
        raise HTTPException(
            status_code=400,
            detail="child_slug, parent_slug and source_url are all required",
        )
    try:
        return kb.set_observation_quarantined(
            request.child_slug,
            request.parent_slug,
            request.source_url,
            request.quarantined,
        )
    except KeyError as e:
        raise HTTPException(
            status_code=404,
            detail=f"No observation row matches: {e.args[0]}",
        )
