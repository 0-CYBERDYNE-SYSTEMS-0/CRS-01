"""Lineage extraction — turn free-text snippets into structured parent-strain tuples.

Patterns (intentionally redundant — each catches a different way breeders
describe crosses on the open web):

  1. "X × Y" / "X x Y"                  — most common in strain databases
  2. "cross of X and Y"
  3. "bred from X/Y" / "bred from X and Y"
  4. "X crossed with Y"
  5. "X and Y" appearing in a "Parents:" / "Genetics:" / "Lineage:" sentence
  6. parenthetical "(X × Y)" in strain descriptions
  7. JSON-LD schema.org parsing (best-effort — most pages lack it)

Returns a list of LineageClaim records, each anchored to a source URL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# Capitalized-word heuristic for strain names. Catches "Blue Dream", "OG Kush",
# "Silver Haze", "Jack Herer", but rejects "The", "A", etc. Allows hyphens,
# digits, single-quotes (for "Jack's"), and ampersands.
_STRAIN_NAME = r"[A-Z][A-Za-z0-9&'\-#]*(?:[ \t]+[A-Z][A-Za-z0-9&'\-#]*){0,2}"

_PATTERNS = [
    # Wikipedia infobox: "| hybrid = Brazilian Sativa ×\n\nSouth Indian Indica"
    re.compile(rf"hybrid\s*=\s*({_STRAIN_NAME})[ \t\n]+[×xX][ \t\n]+({_STRAIN_NAME})", re.I),
    # "Blue Dream × Haze" / "Blue Dream x Haze" / "Blue Dream X Haze"
    re.compile(rf"({_STRAIN_NAME})[ \t]+[×xX][ \t]+({_STRAIN_NAME})"),
    # "cross of Blue Dream and Haze"
    re.compile(rf"cross[ \t]+of[ \t]+({_STRAIN_NAME})[ \t]+and[ \t]+({_STRAIN_NAME})", re.I),
    # "crossing Blue Dream with Haze" / "crossing Blue Dream and Haze"
    re.compile(rf"crossing[ \t]+({_STRAIN_NAME})[ \t]+(?:with|and|x)[ \t]+({_STRAIN_NAME})", re.I),
    # "bred from Blue Dream / Haze"  or  "bred from Blue Dream and Haze"
    re.compile(rf"bred[ \t]+from[ \t]+({_STRAIN_NAME})[ \t]+(?:/|and|with)[ \t]+({_STRAIN_NAME})", re.I),
    # "Blue Dream crossed with Haze"
    re.compile(rf"({_STRAIN_NAME})[ \t]+crossed[ \t]+with[ \t]+({_STRAIN_NAME})", re.I),
    # "made by crossing Blue Dream with Haze" (active-voice variant)
    re.compile(rf"(?:by|via)[ \t]+crossing[ \t]+({_STRAIN_NAME})[ \t]+(?:with|and|x)[ \t]+({_STRAIN_NAME})", re.I),
    # "hybrid of Blue Dream and Haze"
    re.compile(rf"hybrid[ \t]+of[ \t]+({_STRAIN_NAME})[ \t]+and[ \t]+({_STRAIN_NAME})", re.I),
    # "descended from Blue Dream and Haze"
    re.compile(rf"descended[ \t]+from[ \t]+({_STRAIN_NAME})[ \t]+and[ \t]+({_STRAIN_NAME})", re.I),
    # "Parents: Blue Dream, Haze"  or  "Genetics: Blue Dream and Haze"
    re.compile(rf"(?:parents|genetics|lineage|breeders?)\s*:[ \t]*({_STRAIN_NAME}(?:[ \t]*(?:,|/|and)[ \t]+{_STRAIN_NAME}){{0,3}})", re.I),
    # parenthetical "(Blue Dream × Haze)"
    re.compile(rf"\([ \t]*({_STRAIN_NAME})[ \t]+[×xX][ \t]+({_STRAIN_NAME})[ \t]*\)"),
    # "is a cross of White Widow and Blueberry" (Wikipedia body text)
    re.compile(rf"is[ \t]+a[ \t]+cross[ \t]+of[ \t]+({_STRAIN_NAME})[ \t]+and[ \t]+({_STRAIN_NAME})", re.I),
]


@dataclass
class LineageClaim:
    child: str
    parent_a: str
    parent_b: str
    source_url: str
    source_title: str
    source_engine: str
    snippet_excerpt: str = ""
    confidence: float = 0.5
    raw_text: str = ""
    extra_parents: List[str] = field(default_factory=list)
    # Backend-computed trust tier (see orchestrator.claim_tier). VERIFIED is
    # never set here — it is reserved for human curation (2026-08-19).
    tier: Optional[str] = None
    # parent display-name → female|male|parent. Only filled when the
    # excerpt states the role explicitly; never inferred from order.
    parent_roles: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "child": self.child,
            "parent_a": self.parent_a,
            "parent_b": self.parent_b,
            "source_url": self.source_url,
            "source_title": self.source_title,
            "source_engine": self.source_engine,
            "snippet_excerpt": self.snippet_excerpt,
            "confidence": round(self.confidence, 3),
            "raw_text": self.raw_text,
            "extra_parents": self.extra_parents,
            "tier": self.tier,
            "parent_roles": self.parent_roles,
        }


def _normalize_name(name: str) -> str:
    """Normalize a strain name to a slug-like canonical form for dedupe."""
    n = name.strip()
    n = re.sub(r"\s+", " ", n)
    # Strip trailing "strain" / "cultivar" qualifiers
    n = re.sub(r"\s+(strain|cultivar|variety)$", "", n, flags=re.I)
    return n


def _looks_like_name(token: str) -> bool:
    """Reject tokens that are obviously not strain names."""
    if not token or len(token) < 3:
        return False
    # Only reject explicit connectors and verbs — landrace descriptors
    # like "Brazilian Sativa" or "South Indian Indica" ARE valid strain
    # names in Wikipedia infoboxes.
    blacklist = {
        "the", "this", "that", "with", "from", "and", "for", "are", "was",
        "has", "had", "have", "been", "they", "their", "its", "into",
        "such", "some", "any", "all", "most", "more", "less", "very",
        "cross", "bred", "strain",
    }
    tl = token.lower().split()
    if any(w in blacklist for w in tl):
        return False
    return True


def infer_parent_role(parent: str, text: str) -> Optional[str]:
    """Return female/male when ``text`` states the role of ``parent`` explicitly.

    Order in an ``X × Y`` pair is not a signal. Unsexed words like
    "parent" alone are ignored. Missing/blank → None.
    """
    if not parent or not text:
        return None
    n = re.escape(parent.strip())
    if not n:
        return None
    female = (
        rf"(?:female\s+parent|seed\s+parent|mother|dam)"
    )
    male = (
        rf"(?:male\s+parent|pollen\s+(?:parent|donor)|father|sire)"
    )
    patterns = (
        (rf"{n}\s*\(\s*(?:female|mother|dam|seed\s*parent)\s*\)", "female"),
        (rf"{n}\s*\(\s*(?:male|father|sire|pollen\s*(?:parent|donor))\s*\)", "male"),
        (rf"{female}\s*[:\s]+{n}\b", "female"),
        (rf"{male}\s*[:\s]+{n}\b", "male"),
        (rf"{n}\s+(?:is|as)\s+the\s+{female}", "female"),
        (rf"{n}\s+(?:is|as)\s+the\s+{male}", "male"),
        (rf"(?:mothered|mother)\s+by\s+{n}\b", "female"),
        (rf"fathered\s+by\s+{n}\b", "male"),
    )
    for pat, role in patterns:
        if re.search(pat, text, re.I):
            return role
    return None


def _roles_for_parents(parents: List[str], text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p in parents:
        role = infer_parent_role(p, text)
        if role:
            out[p] = role
    return out


def _clean_name(name: str) -> str:
    """Strip leading articles, trailing qualifiers, hash suffix noise."""
    n = name.strip()
    n = re.sub(r"^(?:a|an|the)\s+", "", n, flags=re.I)
    n = re.sub(r"^\s*is\s+", "", n, flags=re.I)
    # Drop hash-suffix that got merged: "Gorilla Glue" from "Gorilla Glue #4"
    n = re.sub(r"\s*#\d+\b.*$", "", n)
    # Drop trailing "strain"/"cultivar"
    n = re.sub(r"\s+(strain|cultivar|variety|landraces?)$", "", n, flags=re.I)
    return n.strip()


def _parse_multi(token: str) -> List[str]:
    """Split a 'X, Y and Z' / 'X / Y' / 'X and Y' token into names."""
    parts = re.split(r"\s*(?:,|/| and |&)\s*", token)
    cleaned: List[str] = []
    for p in parts:
        n = _clean_name(p)
        if n:
            cleaned.append(n)
    return cleaned


def extract_lineage(
    child_hint: str,
    result: Dict[str, Any],
) -> List[LineageClaim]:
    """Extract parent-strain tuples from a single search result.

    `result` must have keys: title, url, snippet, source (engine).
    Returns a list (possibly empty) of LineageClaim records.
    """
    title = result.get("title", "") or ""
    url = result.get("url", "") or ""
    snippet = result.get("snippet", "") or ""
    engine = result.get("source", "unknown")
    text = f"{title}\n{snippet}"
    if not text.strip():
        return []

    claims: List[LineageClaim] = []
    seen: set = set()

    for pat in _PATTERNS:
        for m in pat.finditer(text):
            groups = m.groups()
            # Pattern #5 yields a single group with multiple names
            if len(groups) == 1:
                names = _parse_multi(groups[0])
                if len(names) >= 2:
                    parent_a, parent_b = names[0], names[1]
                else:
                    continue
            else:
                parent_a, parent_b = groups[0], groups[1]

            parent_a = _clean_name(parent_a)
            parent_b = _clean_name(parent_b)

            if not (_looks_like_name(parent_a) and _looks_like_name(parent_b)):
                continue
            if parent_a.lower() == parent_b.lower():
                continue
            # Don't claim a strain is its own parent
            if child_hint and parent_a.lower() == child_hint.lower():
                continue
            if child_hint and parent_b.lower() == child_hint.lower():
                continue

            key = (parent_a.lower(), parent_b.lower())
            if key in seen:
                continue
            seen.add(key)

            excerpt = text[max(0, m.start() - 40):min(len(text), m.end() + 40)].strip()

            # Confidence scales with pattern specificity + snippet richness.
            base = 0.4
            if snippet and len(snippet) > 200:
                base += 0.1
            if "parent" in excerpt.lower() or "genetics" in excerpt.lower():
                base += 0.15
            if "×" in m.group(0) or "crossed" in m.group(0).lower():
                base += 0.1

            extras: List[str] = []
            parent_roles = _roles_for_parents([parent_a, parent_b, *extras], text)
            claims.append(
                LineageClaim(
                    child=child_hint or "",
                    parent_a=parent_a,
                    parent_b=parent_b,
                    source_url=url,
                    source_title=title[:200],
                    source_engine=engine,
                    snippet_excerpt=excerpt[:240],
                    confidence=min(0.95, base),
                    raw_text=m.group(0),
                    parent_roles=parent_roles,
                )
            )

    return claims


def consensus(claims: List[LineageClaim]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Group claims by (parent_a, parent_b) tuple and compute consensus."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for c in claims:
        key = (c.parent_a, c.parent_b)
        entry = out.setdefault(key, {"sources": [], "count": 0, "confidences": []})
        entry["sources"].append(
            {"url": c.source_url, "title": c.source_title, "engine": c.source_engine}
        )
        entry["confidences"].append(c.confidence)
        entry["count"] += 1
    for key, entry in out.items():
        if entry["confidences"]:
            entry["avg_confidence"] = round(sum(entry["confidences"]) / len(entry["confidences"]), 3)
    return out