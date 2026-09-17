"""Agreement semantics — the single implementation of evidence independence.

Locked with the product owner 2026-08-19, sharpened 2026-09-16:

- The consensus unit is the independent REGISTRABLE DOMAIN — never the raw
  URL, and never a provider-synthesized answer. Ten scraped mirrors of one
  seed-bank page are one voice; an LLM answer is zero voices (it synthesizes
  the very pages it would be counted alongside — circular consensus).
- Provider answers are tagged by their pseudo-URL scheme (``tavily://…``,
  ``perplexity://…``). They stay stored and quarantinable like any
  observation, but they can never count toward COMMUNITY_CONSENSUS here.
- One source URL asserts one parent-SET; two assertions conflict only when
  neither set equals nor contains the other (a subset is a partial telling
  of the same story, not a contradiction).

Both derivations — the KB's ``recompute()`` / ``strain_conflicts()`` and the
pipeline's claim-time tiering in the orchestrator — import from this module
so the rule cannot drift between fresh-claim tiers and derived tiers. Pure
functions only: no DB, no network, directly testable.
"""
from __future__ import annotations

from typing import Iterable, Optional, Set
from urllib.parse import urlparse

# Provider pseudo-URL schemes whose rows are LLM synthesis, not observations
# of the web (see providers.py: the "answer" payload becomes a standalone
# result under these URLs). Their netloc is literally "answer", which must
# never be treated as an independent domain.
SYNTHESIZED_URL_SCHEMES = frozenset({"tavily", "perplexity"})

# Public suffixes that need THREE labels to reach a registrable domain
# (bbc.co.uk, not co.uk). Hand-curated and deliberately small — the cannabis
# evidence corpus is not a CDN log. Extend only with a real-world miss.
_MULTI_PART_SUFFIXES = frozenset({
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk",
    "com.au", "net.au", "org.au", "edu.au",
    "co.nz", "net.nz", "org.nz",
    "com.br", "com.mx", "com.ar", "com.co",
    "co.jp", "or.jp", "ne.jp", "ac.jp",
    "com.cn", "com.tw", "com.hk", "com.sg",
    "co.in", "co.za", "com.tr",
})


def is_synthesized_url(url: str) -> bool:
    """True when the URL is a provider-synthesized answer pseudo-URL."""
    try:
        return urlparse(url.strip()).scheme.lower() in SYNTHESIZED_URL_SCHEMES
    except Exception:
        return False


def registrable_domain(url: str) -> Optional[str]:
    """The registrable domain (eTLD+1) a URL belongs to.

    ``www.`` and other subdomains collapse onto their registrable domain
    (``ca.leafly.com`` → ``leafly.com``); ports, case, and path noise are
    ignored. Returns None for provider-synthesized pseudo-URLs — they have
    no domain and never represent an independent voice. Host-less strings
    keep their identity (as before) so unknown schemes degrade gracefully.
    """
    url = (url or "").strip()
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() in SYNTHESIZED_URL_SCHEMES:
            return None
        host = (parsed.hostname or "").lower()
    except Exception:
        host = ""
    if not host:
        return url.lower() or None
    labels = [l for l in host.split(".") if l]
    if not labels:
        return None
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_PART_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else labels[0]


def independent_domains(urls: Iterable[str]) -> Set[str]:
    """Distinct registrable domains across URLs, excluding synthesized ones."""
    domains: Set[str] = set()
    for url in urls or []:
        if not url:
            continue
        domain = registrable_domain(url)
        if domain:
            domains.add(domain)
    return domains


def parent_sets_conflict(a: Iterable[str], b: Iterable[str]) -> bool:
    """Locked conflict rule: two parent-set assertions conflict only when
    neither set equals nor is a subset of the other."""
    fa, fb = frozenset(a), frozenset(b)
    return fa != fb and not fa <= fb and not fb <= fa


def any_parent_sets_conflict(sets: Iterable[Iterable[str]]) -> bool:
    """True when any pair of parent-sets in the collection conflicts."""
    frozen = [frozenset(s) for s in sets or []]
    for i in range(len(frozen)):
        for j in range(i + 1, len(frozen)):
            if parent_sets_conflict(frozen[i], frozen[j]):
                return True
    return False
