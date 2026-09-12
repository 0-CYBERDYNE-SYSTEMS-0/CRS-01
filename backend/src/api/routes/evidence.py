"""Evidence & conflict drill-down — make every verdict auditable.

Backed by the SQLite knowledge base (backend/src/graph/kb.py). The core
promise: users can check WHO says WHAT themselves. These endpoints expose
the raw, non-quarantined ``lineage_sources`` rows behind each lineage edge
and the conflicting parent-set assertions per strain.

Design notes:
- Conflicts are DERIVED on the fly from the append-only evidence rows
  (kb.strain_conflicts mirrors recompute()'s disagreement rule) — never
  persisted, consistent with the locked evidence model of 2026-08-19.
- No TTL cache and no response models: these are cheap raw-row reads and
  the neighboring per-strain routes (graph.py, neighborhood.py) return
  plain dicts too.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from ...graph import kb

router = APIRouter(prefix="/graph", tags=["evidence"])


def _require_strain(slug: str) -> Dict[str, Any]:
    """Resolve a strain node (by slug or name) or raise the shared 404."""
    strain = kb.get_strain(slug)
    if not strain:
        raise HTTPException(
            status_code=404,
            detail={
                "message": f"Strain '{slug}' not found in the knowledge base",
                "suggestions": kb.suggest_similar(slug, limit=4),
            },
        )
    return strain


@router.get("/strains/{slug}/evidence")
async def get_strain_evidence(slug: str):
    """All raw observations behind every lineage edge of a strain,
    grouped by parent, newest first. Nothing is hidden: excerpt, engine,
    confidence and observed_at are included per observation."""
    strain = _require_strain(slug)
    canonical = str(strain["data"]["slug"])
    return {
        "slug": canonical,
        "child_name": strain["data"]["name"],
        "edges": kb.edge_evidence(canonical),
    }


@router.get("/strains/{slug}/conflicts")
async def get_strain_conflicts(slug: str):
    """Enumerable list of conflicting parent-set assertions, with the
    sources on each side. [] when the strain is clean."""
    strain = _require_strain(slug)
    canonical = str(strain["data"]["slug"])
    conflicts = kb.strain_conflicts(canonical)
    return {
        "slug": canonical,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
    }
