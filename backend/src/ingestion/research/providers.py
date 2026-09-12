"""Provider abstractions for Tavily, Perplexity, DuckDuckGo, and Wikipedia.

All providers implement ``ProviderResult`` shape:
    {"title": str, "url": str, "snippet": str, "source": str,
     "score": float | None, "image_url": str | None}

Activation:
  - TAVILY_API_KEY present       → TavilyProvider (live, billed, free trial)
  - PERPLEXITY_API_KEY present   → PerplexityProvider (live, billed, free trial)
  - DuckDuckGo                   → (always on, free HTML search, no key)
  - Wikipedia                    → (always on, free, summary + full-article)

All providers have a 10–30s timeout and gracefully degrade to an empty list
on network errors so one provider's failure doesn't sink the whole research
run.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol
import logging
import re
from urllib.parse import unquote

import httpx
import time

logger = logging.getLogger(__name__)


@dataclass
class ProviderResult:
    title: str
    url: str
    snippet: str
    source: str
    score: Optional[float] = None
    image_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "score": self.score,
            "image_url": self.image_url,
        }


class ResearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int = 10) -> List[ProviderResult]: ...


# ---------------------------------------------------------------------------
# Tavily — billed, breadth-first web search (Bearer header)
# ---------------------------------------------------------------------------

class TavilyProvider:
    name = "tavily"

    def __init__(self, api_key: str, timeout: float = 15.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, limit: int = 10) -> List[ProviderResult]:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    "https://api.tavily.com/search",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "query": query,
                        "max_results": min(limit, 20),
                        "include_answer": True,
                        "search_depth": "advanced",
                    },
                )
                if resp.status_code != 200:
                    logger.warning("Tavily returned HTTP %s", resp.status_code)
                    return []
                data = resp.json()
        except Exception as e:
            logger.warning("Tavily network error: %s", e)
            return []

        results: List[ProviderResult] = []
        for r in data.get("results", []) or []:
            results.append(
                ProviderResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                    source=self.name,
                    score=r.get("score"),
                )
            )
        # Synthesized answer as a standalone result.
        answer = data.get("answer")
        if answer:
            results.insert(
                0,
                ProviderResult(
                    title=f"Tavily answer: {query}",
                    url=f"tavily://answer/{query.replace(' ', '_')}",
                    snippet=answer,
                    source=f"{self.name}-answer",
                    score=None,
                ),
            )
        return results


# ---------------------------------------------------------------------------
# Perplexity — chat/completions sonar model (Bearer auth)
# ---------------------------------------------------------------------------

_DEEP_RESEARCH_PROMPT = (
    "You are a cannabis lineage research assistant. For the query below, "
    "search the web and return a detailed, source-anchored analysis of the "
    "strain's parentage (genetic lineage). Report every parent strain named, "
    "with the source URLs that assert each relationship. Note any disagreements "
    "between sources. Also note: strain type (indica/sativa/hybrid), breeder "
    "name if known, THC range if stated, and a one-paragraph factual summary."
)

# Circuit breaker: a rejected key (401/403) will not start working
# mid-run, so stop paying the ~10s timeout on every query. Transient
# failures get a short backoff instead so one blip doesn't kill the
# provider for the rest of the run.
_PPLX_AUTH_COOLDOWN_S = 900
_PPLX_TRANSIENT_COOLDOWN_S = 60
_pplx_auth_block_until = 0.0
_pplx_transient_block_until = 0.0


def perplexity_is_disabled() -> bool:
    """True when the circuit breaker has tripped (auth or transient backoff)."""
    now = time.monotonic()
    return now < _pplx_auth_block_until or now < _pplx_transient_block_until


def note_perplexity_auth_failure() -> None:
    """Trip the auth breaker after a rejected key (401/403)."""
    global _pplx_auth_block_until
    _pplx_auth_block_until = time.monotonic() + _PPLX_AUTH_COOLDOWN_S
    logger.warning(
        "Perplexity auth failed — skipping provider for %ss (check PERPLEXITY_API_KEY)",
        _PPLX_AUTH_COOLDOWN_S,
    )


class PerplexityProvider:
    name = "perplexity"

    def __init__(self, api_key: str, timeout: float = 45.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def search(self, query: str, limit: int = 10) -> List[ProviderResult]:
        global _pplx_transient_block_until
        if perplexity_is_disabled():
            return []
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "sonar",
                        "messages": [
                            {"role": "system", "content": _DEEP_RESEARCH_PROMPT},
                            {"role": "user", "content": query},
                        ],
                        "temperature": 0,
                    },
                )
                if resp.status_code != 200:
                    if resp.status_code in (401, 403):
                        note_perplexity_auth_failure()
                    else:
                        _pplx_transient_block_until = (
                            time.monotonic() + _PPLX_TRANSIENT_COOLDOWN_S
                        )
                        logger.warning("Perplexity returned HTTP %s", resp.status_code)
                    return []
                data = resp.json()
        except Exception as e:
            _pplx_transient_block_until = time.monotonic() + _PPLX_TRANSIENT_COOLDOWN_S
            logger.warning("Perplexity network error: %s", e)
            return []

        results: List[ProviderResult] = []

        # The model's synthesized answer becomes a rich snippet.
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            content = ""
        if content:
            results.append(
                ProviderResult(
                    title=f"Perplexity answer: {query}",
                    url=f"perplexity://answer/{query.replace(' ', '_')}",
                    snippet=content,
                    source=f"{self.name}-answer",
                    score=None,
                )
            )

        # Citations become individual results the extractor can parse.
        citations = data.get("citations") or []
        for i, url in enumerate(citations[:limit]):
            results.append(
                ProviderResult(
                    title=f"Perplexity citation {i+1}",
                    url=url,
                    snippet="",
                    source=self.name,
                    score=None,
                )
            )

        return results


# ---------------------------------------------------------------------------
# DuckDuckGo — free HTML endpoint (no key, rate-limited but works)
# ---------------------------------------------------------------------------

_DDG_UA = "CRS-01-Research/1.0 (educational; duckduckgo)"
_DDG_LINK_RE = re.compile(
    r'<a[^>]*\bclass\s*=\s*"[^"]*result__a[^"]*"[^>]*\bhref\s*=\s*"([^"]+)"[^>]*>'
    r'((?:(?!</a>).)*)</a>',
    re.S,
)
_DDG_SNIPPET_RE = re.compile(
    r'<a[^>]*\bclass\s*=\s*"[^"]*result__snippet[^"]*"[^>]*>'
    r'((?:(?!</a>).)*)</a>',
    re.S,
)


def _ddg_strip(t: str) -> str:
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    t = t.replace("&#39;", "'").replace("&quot;", '"').replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", t).strip()


def _ddg_decode_url(href: str) -> str:
    """DuckDuckGo wraps result URLs in a redirect — extract the real target."""
    m = re.search(r"[?&]uddg=([^&]+)", href)
    if m:
        return unquote(m.group(1))
    return href


class DuckDuckGoProvider:
    name = "duckduckgo"

    def __init__(self, timeout: float = 12.0) -> None:
        self.timeout = timeout

    def search(self, query: str, limit: int = 10) -> List[ProviderResult]:
        try:
            with httpx.Client(timeout=self.timeout, headers={"User-Agent": _DDG_UA}) as client:
                resp = client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": query},
                    follow_redirects=True,
                )
                if resp.status_code != 200:
                    logger.warning("DDG returned HTTP %s", resp.status_code)
                    return []
                html = resp.text
        except Exception as e:
            logger.warning("DDG network error: %s", e)
            return []

        # Parse result blocks: each result__body contains a title link
        # (result__a) and optionally a snippet (result__snippet).
        # We heuristically pair them by scanning the HTML linearly.
        results: List[ProviderResult] = []

        # Split on result__body blocks.
        bodies = re.split(
            r'<[^>]*\bclass\s*=\s*"[^"]*\bresults_links[^"]*"[^>]*>', html
        )
        # The first chunk is the header; the rest are results.
        for block in bodies[1:]:
            if len(results) >= limit:
                break
            a_m = _DDG_LINK_RE.search(block)
            s_m = _DDG_SNIPPET_RE.search(block)
            if not a_m:
                continue
            url = _ddg_decode_url(a_m.group(1))
            title = _ddg_strip(a_m.group(2))
            snippet = _ddg_strip(s_m.group(1)) if s_m else ""
            if not url or not title:
                continue
            results.append(
                ProviderResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source=self.name,
                )
            )

        return results


# ---------------------------------------------------------------------------
# Wikipedia — free, full-article + summary fetch
# ---------------------------------------------------------------------------

class WikipediaProvider:
    name = "wikipedia"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout
        self.ua = "CRS-01-Research/1.0 (educational; contact: testers@crs01.local)"

    def _get_with_retry(
        self, client: httpx.Client, url: str, params: dict = None
    ) -> Optional[httpx.Response]:
        for attempt in range(3):
            try:
                resp = client.get(url, params=params)
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", "2"))
                    time.sleep(min(wait, 5))
                    continue
                return resp
            except Exception as e:
                logger.warning("Wikipedia GET error (attempt %s): %s", attempt, e)
                time.sleep(1)
        return None

    def search(self, query: str, limit: int = 10) -> List[ProviderResult]:
        try:
            with httpx.Client(
                timeout=self.timeout, headers={"User-Agent": self.ua}
            ) as client:
                r1 = self._get_with_retry(
                    client,
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "opensearch",
                        "format": "json",
                        "search": query,
                        "limit": str(max(limit * 2, 6)),
                        "namespace": "0",
                    },
                )
                if r1 is None:
                    return []
                titles = (r1.json() or [[]])[1] or []

                results: List[ProviderResult] = []
                for title in titles:
                    if len(results) >= limit:
                        break
                    r2 = self._get_with_retry(
                        client,
                        f"https://en.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}",
                    )
                    if r2 is None or r2.status_code != 200:
                        continue
                    data = r2.json()
                    extract = (data.get("extract") or "").strip()
                    if not extract:
                        continue
                    page_url = (
                        (data.get("content_urls") or {})
                        .get("desktop", {})
                        .get("page")
                        or f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}"
                    )
                    # Capture thumbnail for strain images.
                    thumb = (data.get("thumbnail") or {}).get("source")
                    original = (data.get("originalimage") or {}).get("source")
                    image_url = original or thumb or None
                    results.append(
                        ProviderResult(
                            title=data.get("title") or title,
                            url=page_url,
                            snippet=extract,
                            source=self.name,
                            score=None,
                            image_url=image_url,
                        )
                    )
                return results
        except Exception as e:
            logger.warning("Wikipedia network error: %s", e)
            return []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_research_providers() -> List[ResearchProvider]:
    """Return active providers in priority order.

    Order:
      1. Tavily       (if TAVILY_API_KEY set)
      2. Perplexity   (if PERPLEXITY_API_KEY set)
      3. DuckDuckGo   (always, free)
      4. Wikipedia    (always, free fallback)
    """
    providers: List[ResearchProvider] = []
    tavily_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if tavily_key:
        providers.append(TavilyProvider(tavily_key))
    pplx_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if pplx_key:
        providers.append(PerplexityProvider(pplx_key))
    providers.append(DuckDuckGoProvider())  # free, no key
    providers.append(WikipediaProvider())   # always-on fallback
    return providers
