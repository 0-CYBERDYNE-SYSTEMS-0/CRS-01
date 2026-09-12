"""Fetch full Wikipedia article content (not just summary) for lineage parsing.

The summary endpoint returns ~500 chars; the full article contains the
infobox with `genetics` / `parents` fields — the most authoritative
machine-readable source for a strain's lineage on the open web.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

UA = "CRS-01-Research/1.0 (educational; contact: testers@crs01.local)"


def _strip_html_to_text(html: str) -> str:
    """Cheap HTML stripper — no external deps."""
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
    )
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _find_infobox_blocks(s: str) -> List[tuple]:
    """Find `{{Infobox ... }}` blocks using brace balancing."""
    blocks: List[tuple] = []
    i = 0
    while i < len(s):
        idx = s.lower().find("{{infobox", i)
        if idx < 0:
            break
        depth = 0
        j = idx
        while j < len(s) - 1:
            two = s[j : j + 2]
            if two == "{{":
                depth += 1
                j += 2
            elif two == "}}":
                depth -= 1
                j += 2
                if depth == 0:
                    blocks.append((idx, j))
                    i = j
                    break
            else:
                j += 1
        else:
            break
    return blocks


def _flatten_infobox(raw: str) -> str:
    """Convert an `{{Infobox ... }}` block into flat `key = value` lines.

    The output preserves the infobox content (where lineage data lives)
    while stripping nested templates and wikilink wrappers. Multi-line
    values (like `| hybrid = X ×\n\nY`) are merged into a single line.
    """
    m = re.match(r"\s*\{\{\s*[Ii]nfobox[^\n]*\n", raw)
    if not m:
        return raw
    inner = raw[m.end():]
    if inner.rstrip().endswith("}}"):
        inner = inner.rstrip()[:-2]
    # Strip nested templates inside (recursively)
    for _ in range(10):
        prev = inner
        inner = re.sub(r"\{\{[^{}]*\}\}", "", inner)
        if inner == prev:
            break

    # Walk the lines, tracking the current key. Continuation lines
    # (those starting with whitespace or with no leading `|`) get
    # appended to the current key's value.
    pairs: Dict[str, List[str]] = {}
    order: List[str] = []
    current_key: Optional[str] = None
    for line in inner.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("|"):
            stripped = stripped[1:].strip()
            if "=" in stripped:
                key, _, value = stripped.partition("=")
                key = key.strip()
                value = value.strip()
                if key and key not in pairs:
                    pairs[key] = []
                    order.append(key)
                current_key = key
                if value:
                    pairs[key].append(value)
        elif current_key is not None:
            # Continuation line
            pairs[current_key].append(stripped)

    lines: List[str] = []
    for key in order:
        value = " ".join(pairs.get(key, []))
        # Drop wikilink wrappers
        value = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", value)
        # Drop italic/bold markers
        value = value.replace("'''", "").replace("''", "")
        # Collapse whitespace
        value = re.sub(r"\s+", " ", value).strip()
        if value:
            lines.append(f"{key} = {value}")
    return "\n".join(lines)


def _strip_wikitext(text: str) -> str:
    """Wikitext → plain text. Preserves infobox content."""
    blocks = _find_infobox_blocks(text)
    infoboxes_flat = [_flatten_infobox(text[s:e]) for s, e in blocks]

    # Replace each infobox with a sentinel
    sentinel_text = text
    for idx, (s, e) in enumerate(blocks):
        sentinel_text = sentinel_text.replace(text[s:e], f"\n\n__INFOBOX_{idx}__\n\n", 1)

    # Strip remaining (non-infobox) simple templates
    for _ in range(10):
        prev = sentinel_text
        sentinel_text = re.sub(r"\{\{[^{}]*\}\}", "", sentinel_text)
        if sentinel_text == prev:
            break

    # Restore infoboxes (as flat key=value text)
    for idx, flat in enumerate(infoboxes_flat):
        sentinel_text = sentinel_text.replace(f"__INFOBOX_{idx}__", flat, 1)

    # Drop HTML tags, fix wikilinks, collapse whitespace
    sentinel_text = re.sub(r"<[^>]+>", "", sentinel_text)
    sentinel_text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", sentinel_text)
    sentinel_text = sentinel_text.replace("'''", "").replace("''", "")
    sentinel_text = re.sub(r"[ \t]+", " ", sentinel_text)
    return sentinel_text.strip()


def fetch_wikipedia_full(title: str, timeout: float = 10.0) -> Optional[str]:
    """Fetch the plain-text extract of a Wikipedia article via action=parse.

    Returns the wikitext (downgraded to plain text), or None on any failure.
    The text is suitable for lineage extraction via `extractor.extract_lineage`.
    """
    if not title or not title.strip():
        return None
    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": UA}) as client:
            r = client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "parse",
                    "format": "json",
                    "page": title,
                    "prop": "wikitext",
                    "redirects": "1",
                },
            )
            if r.status_code != 200:
                logger.info("Wikipedia full fetch %s → HTTP %s", title, r.status_code)
                return None
            data = r.json()
            if "error" in data:
                return None
            wikitext = (data.get("parse") or {}).get("wikitext", {}).get("*", "")
            if not wikitext:
                return None
            return _strip_wikitext(wikitext)
    except Exception as e:
        logger.warning("Wikipedia full fetch error for %s: %s", title, e)
        return None


def fetch_wikipedia_extract(title: str, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """Fetch the structured summary for a Wikipedia article.

    Returns the parsed JSON dict (with keys like `extract`, `description`,
    `content_urls.desktop.page`) or None on any failure.
    """
    if not title or not title.strip():
        return None
    try:
        with httpx.Client(timeout=timeout, headers={"User-Agent": UA}) as client:
            r = client.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}",
            )
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as e:
        logger.warning("Wikipedia summary fetch error for %s: %s", title, e)
        return None