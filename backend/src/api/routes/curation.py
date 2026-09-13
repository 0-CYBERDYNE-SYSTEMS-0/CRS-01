"""Human curation endpoints — the only path to VERIFIED.

The locked evidence model (2026-08-19) reserves VERIFIED for human
curation and never lets the pipeline assign it. These routes are that
human loop:

- POST /graph/strains/{slug}/review     — set/clear a human trust tier
- POST /graph/observations/quarantine   — flag a bad raw observation out
                                          of every aggregate
- GET  /graph/parents/pending           — the unresolved-parent gate queue
- POST /graph/parents/{slug}/resolve    — approve a gated parent name
                                          (or alias it onto an existing slug)
- POST /graph/strains/{slug}/aliases    — curator add/remove of aliases

Both review and resolve are plain-dict responses (no response models),
matching the neighboring per-strain routes in graph.py / evidence.py.
CONTRADICTED is deliberately not assignable here: it is a data-derived
verdict (conflicting parent sets), not a curation choice — it arrives and
leaves with the evidence.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...graph import kb
from .evidence import _require_strain
from .neighborhood import purge_cache

router = APIRouter(prefix="/graph", tags=["curation"])


class ReviewRequest(BaseModel):
    tier: Optional[str] = None
    note: str = Field(default="", max_length=2000)


class QuarantineRequest(BaseModel):
    child_slug: str
    parent_slug: str
    source_url: str
    quarantined: bool = True


class ResolveParentRequest(BaseModel):
    name: str = Field(default="", max_length=200)
    alias_of: str = Field(
        default="",
        max_length=200,
        description=(
            "If set, record the pending name as an alias of this existing "
            "strain slug instead of materializing a new node."
        ),
    )


class AliasRequest(BaseModel):
    add: List[str] = Field(default_factory=list)
    remove: List[str] = Field(default_factory=list)


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
    purge_cache()
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
        result = kb.set_observation_quarantined(
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
    purge_cache()
    return result


@router.get("/parents/pending")
async def list_pending_parents(limit: int = 100):
    """The unresolved-parent gate queue: raw observations naming parents the
    KB has never seen, grouped by parent name. These assertions are on file
    but invisible to every aggregate until the parent is resolved."""
    return kb.pending_parents(limit=limit)


@router.post("/parents/{parent_slug}/resolve")
async def resolve_pending_parent(parent_slug: str, request: ResolveParentRequest):
    """Approve a gated parent name.

    Default: materialize the strain node and release every gated
    observation citing it. ``name`` optionally supplies the display name
    (default: title-cased slug).

    ``alias_of``: treat the pending name as an alias of an existing
    strain instead of creating a sibling node. Raw observation slugs
    stay as recorded; derived aggregates join through the alias map.
    """
    try:
        result = kb.resolve_parent(
            parent_slug,
            request.name or "",
            alias_of=request.alias_of or "",
        )
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e.args[0]))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    purge_cache()
    return result


@router.post("/strains/{slug}/aliases")
async def update_strain_aliases(slug: str, request: AliasRequest):
    """Add or remove normalized aliases on an existing strain.

    Adding an alias that currently sits in the unresolved-parent queue
    releases those observations. Cannot claim another strain's slug.
    """
    _require_strain(slug)
    try:
        result = kb.set_strain_aliases(slug, add=request.add, remove=request.remove)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e.args[0]))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    purge_cache()
    return result
