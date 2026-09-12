"""Strain-name canonicalization for lineage claims.

The same cultivar arrives under many spellings ("GSC", "Cookies",
"Girl Scout Cookies"). Without a canonical form the graph fragments into
sibling nodes and consensus votes split — three sources agreeing on
GMO = Chem D × GSC/Cookies/GSC counted as three single-source anecdotes
instead of one consensus. Every claim's child and parent names pass
through ``canonical_strain_name`` before entering a ResearchRun, and
``is_junk_child_name`` drops claims where the extracted "child" is
clearly not a strain (e.g. "OG Kush hybrid" scraped from a blog post
listing modern OG crosses).
"""
from __future__ import annotations

import re
from typing import Optional

# lowercase lookup-key → canonical display name.
_ALIASES: dict[str, str] = {
    "gsc": "Girl Scout Cookies",
    "gs cookies": "Girl Scout Cookies",
    "girl scout cookie": "Girl Scout Cookies",
    "girl scout cookies": "Girl Scout Cookies",
    "cookies": "Girl Scout Cookies",
    "cookie": "Girl Scout Cookies",
    "thin mint": "Thin Mints",
    "gdp": "Granddaddy Purple",
    "grandaddy purple": "Granddaddy Purple",
    "grand daddy purple": "Granddaddy Purple",
    "granddaddy purps": "Granddaddy Purple",
    "chemdawg": "Chemdawg",
    "chem dog": "Chemdawg",
    "chem-dog": "Chemdawg",
    "chem d": "Chemdawg",
    "chem-d": "Chemdawg",
    "chem": "Chemdawg",
    "chemdawg d": "Chemdawg",
    "og": "OG Kush",
    "original gangster": "OG Kush",
    "sour d": "Sour Diesel",
    "sour deisel": "Sour Diesel",
    "northen lights": "Northern Lights",
    "northern light": "Northern Lights",
    "ak47": "AK-47",
}

# Tokens that mark an extracted *child* as prose rather than a cultivar.
_JUNK_CHILD_TOKENS = (
    "hybrid",
    "unknown",
    "unnamed",
    "mystery",
    "various",
    "modern",
    "crosses",
    "characteristics",
)

_WS_RE = re.compile(r"\s+")


def _lookup_key(name: str) -> str:
    """Fold punctuation/case so 'chem-dog', 'Chem Dog', 'chemdog' collide."""
    key = name.lower().strip().replace("-", " ").replace("'", "").replace("’", "")
    return _WS_RE.sub(" ", key)


def canonical_strain_name(name: str) -> str:
    """Return the canonical display name for a strain, or the cleaned input."""
    if not name:
        return name
    cleaned = _WS_RE.sub(" ", name.strip())
    if not cleaned:
        return cleaned
    return _ALIASES.get(_lookup_key(cleaned), cleaned)


def is_junk_child_name(child: str, subject_query: str) -> bool:
    """True when a claim's child is obviously not a strain name.

    The subject itself is always allowed (the user asked for it), so a
    genuinely odd query still renders its own graph.
    """
    c = _lookup_key(child)
    q = _lookup_key(subject_query)
    if not c or c == q:
        return False
    return any(tok in c.split() for tok in _JUNK_CHILD_TOKENS)


# Fragments that mark a *parent* name as scraped prose rather than a
# cultivar ("known in some sources as Chemdawg" → "sources as Chemdawg").
_JUNK_PARENT_TOKENS = (
    "sources",
    "source",
    "referred",
    "known",
    "called",
    "named",
    "also",
    "some",
)


def is_junk_parent_name(name: str) -> bool:
    """True when an extracted parent name contains prose fragments."""
    p = _lookup_key(name)
    if not p:
        return True
    return any(tok in p.split() for tok in _JUNK_PARENT_TOKENS)


# ---------------------------------------------------------------------------
# Topic gate — permissive cannabis hints (moved here from the deleted
# ingestion/search_provider.py; the orchestrator's filter uses it to drop
# obviously-unrelated pages before extraction). Deliberately looser than the
# orchestrator's strong-token gate (_looks_strongly_cannabis): this one only
# has to reject films/cities, so generic words like "genetics" are allowed.
# ---------------------------------------------------------------------------

_CANNABIS_HINTS = (
    "cannabis", "marijuana", "hemp", "thc", "cbd", "terpene",
    "indica", "sativa", "ruderalis", "strain", "cultivar", "kush",
    "haze", "kush", "genetics", "breed", "hybrid",
)


def _looks_like_cannabis(title: str, description: Optional[str], extract: str) -> bool:
    haystack = f"{title} {description or ''} {extract}".lower()
    return any(h in haystack for h in _CANNABIS_HINTS)
