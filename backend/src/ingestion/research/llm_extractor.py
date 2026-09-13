"""LLM-backed lineage extraction.

When an API key is configured (``PERPLEXITY_API_KEY`` preferred,
``OPENAI_API_KEY`` as fallback), this module asks an LLM to read the
gathered search results and return strict JSON lineage claims with
source-index anchoring so every claim stays attributable.

Without any key the extractor is skipped and callers fall back to
the regex extractor. Nothing here is allowed to raise.

Provider order:
  1. Perplexity (sonar models) — if PERPLEXITY_API_KEY is set
  2. OpenAI (GPT-4o-mini or CRS_LLM_MODEL) — if OPENAI_API_KEY is set
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from . import providers

logger = logging.getLogger(__name__)

_MAX_CHARS_PER_RESULT = 2400
_MAX_TOTAL_CHARS = 14000

_SYSTEM_PROMPT = """\
You are a rigorous cannabis-cultivar research assistant. You extract lineage
(parentage) claims from web-search evidence. Rules:
- Report ONLY what the provided sources state. Never invent names, URLs, or numbers.
- A cross may have 2 or 3 (occasionally more) parents — report all of them.
- Each lineage row MUST cite a source_index (0-based) into the provided results.
- confidence: 0.0-1.0 — how explicitly the source states this parentage.
- parent_roles is optional: a map of parent name → "female" | "male" ONLY
  when the cited source explicitly states sex/role (mother/father, female
  parent, pollen donor, seed parent). Never infer from left/right order.
  Omit the field when the source does not say.
- metadata describes the SUBJECT strain only; omit fields the sources don't state.
- Respond with strict JSON only, no prose, no markdown fences.\
"""


@dataclass
class LLMExtraction:
    """Structured result of one LLM extraction pass."""
    lineage: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    model: str = ""
    # Token accounting from the provider's usage block. Captured even when
    # the response fails to parse into claims — spend is real whether or not
    # the extraction produced anything.
    usage: Dict[str, int] = field(default_factory=dict)

    @property
    def used(self) -> bool:
        return bool(self.lineage) or bool(self.metadata)


def llm_configured() -> bool:
    return bool(
        os.environ.get("OPENAI_API_KEY", "").strip()
        or os.environ.get("PERPLEXITY_API_KEY", "").strip()
    )


def _provider_config() -> tuple[str, str, str]:
    """Return (base_url, api_key, model) — prefers Perplexity if key is set."""
    pplx_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if pplx_key:
        model = os.environ.get("CRS_LLM_MODEL", "sonar").strip()
        # Perplexity only accepts sonar-family models.
        if not model.startswith("sonar"):
            model = "sonar"
        return ("https://api.perplexity.ai", pplx_key, model)
    return (
        os.environ.get("CRS_LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
        os.environ.get("OPENAI_API_KEY", "").strip(),
        os.environ.get("CRS_LLM_MODEL", "gpt-4o-mini"),
    )


def _base_url() -> str:
    return _provider_config()[0]


def _model() -> str:
    return _provider_config()[2]


def _is_perplexity() -> bool:
    return bool(os.environ.get("PERPLEXITY_API_KEY", "").strip())


def _build_user_prompt(query: str, results: List[Dict[str, Any]]) -> str:
    blocks: List[str] = []
    total = 0
    for i, r in enumerate(results):
        text = f"{r.get('title', '')}\n{r.get('snippet', '')}"[:_MAX_CHARS_PER_RESULT]
        if total + len(text) > _MAX_TOTAL_CHARS:
            break
        total += len(text)
        blocks.append(f"--- result[{i}] url={r.get('url', '')} ---\n{text}")
    evidence = "\n\n".join(blocks)
    return (
        f"Subject strain: \"{query}\"\n\n"
        f"Search evidence below. Extract the lineage of the subject (and of any\n"
        f"other strain named in the evidence, each with its own source_index).\n"
        f"Also extract subject metadata when stated.\n\n{evidence}\n\n"
        'Return JSON: {"subject": str, "lineage": [{"child": str, '
        '"parents": [str, ...], "parent_roles": {"Name": "female"|"male"}, '
        '"evidence": str, "source_index": int, '
        '"confidence": float}], "metadata": {"summary": str, '
        '"strain_type": str, "thc_range": str, "breeder": str, '
        '"image_url": str}}'
    )


def _parse_response(raw: str, query: str, results: List[Dict[str, Any]]) -> LLMExtraction:
    """Parse the model's JSON into lineage rows anchored to real URLs."""
    out = LLMExtraction()
    text = raw.strip()
    # Tolerate markdown fences if the model added them despite instructions.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("LLM extractor: unparseable response for %r", query)
        return out

    for row in data.get("lineage") or []:
        try:
            child = str(row.get("child") or query).strip()
            parents = [str(p).strip() for p in (row.get("parents") or []) if str(p).strip()]
            idx = int(row.get("source_index", -1))
        except (TypeError, ValueError):
            continue
        if not child or len(parents) < 1:
            continue
        if idx < 0 or idx >= len(results):
            # Unanchored claim — drop it. Provenance is non-negotiable.
            continue
        src = results[idx]
        if not src.get("url"):
            continue
        try:
            conf = float(row.get("confidence", 0.6))
        except (TypeError, ValueError):
            conf = 0.6
        parent_roles: Dict[str, str] = {}
        raw_roles = row.get("parent_roles") or {}
        if isinstance(raw_roles, dict):
            allowed = {p.lower(): p for p in parents}
            for k, v in raw_roles.items():
                name = str(k).strip()
                role = str(v).strip().lower()
                if role not in ("female", "male"):
                    continue
                match = allowed.get(name.lower())
                if match:
                    parent_roles[match] = role
        out.lineage.append({
            "child": child,
            "parents": parents[:4],
            "source_url": src["url"],
            "source_title": str(src.get("title", ""))[:200],
            "source_engine": str(src.get("source", src.get("engine", "llm"))),
            "snippet_excerpt": str(row.get("evidence", ""))[:240],
            "confidence": max(0.05, min(0.98, conf)),
            "parent_roles": parent_roles,
        })

    meta = data.get("metadata") or {}
    if isinstance(meta, dict):
        for key in ("summary", "strain_type", "thc_range", "breeder", "image_url"):
            v = meta.get(key)
            if isinstance(v, str) and v.strip() and len(v.strip()) < 1200:
                out.metadata[key] = v.strip()
    return out


def extract_with_llm(
    query: str,
    results: List[Dict[str, Any]],
    timeout: float = 45.0,
) -> Optional[LLMExtraction]:
    """Run one extraction pass. Returns None when unavailable/failed —
    callers then fall back to the regex extractor."""
    base_url, api_key, model = _provider_config()
    if not api_key or not query.strip() or not results:
        return None
    # Share the provider circuit breaker so a dead Perplexity key fails
    # fast here too instead of re-sending every query into a 401.
    if _is_perplexity() and providers.perplexity_is_disabled():
        return None

    prompt = _build_user_prompt(query, results)
    is_pplx = _is_perplexity()

    request_body: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
    }
    # Perplexity doesn't support response_format — rely on the system prompt
    # to enforce JSON output (plus the markdown-fence tolerance in _parse_response).
    if not is_pplx:
        request_body["response_format"] = {"type": "json_object"}

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=request_body,
            )
            if resp.status_code != 200:
                if _is_perplexity() and resp.status_code in (401, 403):
                    providers.note_perplexity_auth_failure()
                logger.warning("LLM extractor HTTP %s for %r", resp.status_code, query)
                return None
            data = resp.json()
    except Exception as e:
        logger.warning("LLM extractor error for %r: %s", query, e)
        return None

    try:
        content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        logger.warning("LLM extractor: unexpected payload shape for %r", query)
        return None

    out = _parse_response(content, query, results)
    out.model = _model()
    usage = data.get("usage") or {}
    if isinstance(usage, dict):
        out.usage = {
            k: int(usage[k])
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
            if isinstance(usage.get(k), (int, float))
        }
    if out.used:
        logger.info(
            "LLM extractor: %d lineage rows, %d meta fields for %r",
            len(out.lineage), len(out.metadata), query,
        )
    return out
