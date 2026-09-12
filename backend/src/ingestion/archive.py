"""Source durability — real Wayback Machine availability integration.

Replaces the Phase-1 stub: CRS-01 now asks archive.org whether a capture
of a source URL already exists and records exactly what it was told. It
never *submits* pages for archiving and never claims a capture it didn't
observe — a failed lookup is "not archived", never an error to hide.

Design notes
------------
- ``wayback_availability`` fails OPEN: any failure (offline, timeout,
  non-200, malformed JSON, unexpected shape) returns ``{archived: False}``
  and never raises to the caller. An archive check must never take down a
  research merge or an API request.
- The Wayback snapshot timestamp (``YYYYMMDDhhmmss``) is converted to a
  UTC epoch int so it stores cleanly in the KB's ``archived_at INTEGER``
  column; an unparseable timestamp is stored as NULL rather than guessed.
- ``autolookup_run_sources`` is the opt-in (``CRS_WAYBACK_AUTOLOOKUP``,
  default off) post-merge hook: it checks up to 10 NEW source URLs from a
  run and persists hits. It is wrapped per-URL so it can never fail the
  merge that called it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

WAYBACK_AVAILABILITY_URL = "https://archive.org/wayback/available"
WAYBACK_TIMEOUT_SECONDS = 4.0

# Auto-lookup opt-in (default OFF — no network in the merge path). The
# canonical setting lives in config.Settings (env alias CRS_WAYBACK_AUTOLOOKUP).
AUTOLOOKUP_ENV = "CRS_WAYBACK_AUTOLOOKUP"
AUTOLOOKUP_MAX_URLS = 10


def _autolookup_enabled() -> bool:
    return bool(settings.wayback_autolookup)


def wayback_timestamp_to_epoch(ts: Any) -> Optional[int]:
    """Wayback timestamps (``YYYYMMDDhhmmss``, possibly truncated) → UTC
    epoch seconds. None when unparseable — never a guessed value."""
    try:
        digits = "".join(c for c in str(ts) if c.isdigit())
        for n, fmt in ((14, "%Y%m%d%H%M%S"), (12, "%Y%m%d%H%M"),
                       (10, "%Y%m%d%H"), (8, "%Y%m%d"), (6, "%Y%m"),
                       (4, "%Y")):
            if len(digits) >= n:
                try:
                    dt = datetime.strptime(digits[:n], fmt).replace(
                        tzinfo=timezone.utc
                    )
                    return int(dt.timestamp())
                except ValueError:
                    continue
        return None
    except Exception:
        return None


def wayback_availability(url: str) -> Dict[str, Any]:
    """Ask archive.org whether a capture of ``url`` exists.

    Returns ``{archived: True, wayback_url, archived_at, available_status}``
    on a hit (``archived_at`` = epoch seconds from the snapshot timestamp)
    or ``{archived: False}`` on a miss or ANY failure. Never raises.
    """
    try:
        resp = httpx.get(
            WAYBACK_AVAILABILITY_URL,
            params={"url": url},
            timeout=WAYBACK_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            logger.info(
                "wayback availability for %s returned HTTP %s", url, resp.status_code
            )
            return {"archived": False}
        data = resp.json()
        closest = (data.get("archived_snapshots") or {}).get("closest") or {}
        wayback_url = closest.get("url")
        if not closest or not wayback_url:
            return {"archived": False}
        return {
            "archived": True,
            "wayback_url": wayback_url,
            "archived_at": wayback_timestamp_to_epoch(closest.get("timestamp")),
            "available_status": closest.get("status"),
        }
    except Exception as exc:  # offline, timeout, malformed JSON, …
        logger.info("wayback availability lookup failed for %s: %s", url, exc)
        return {"archived": False}


def autolookup_run_sources(run: Dict[str, Any], limit: int = AUTOLOOKUP_MAX_URLS) -> int:
    """Opt-in post-merge hook: record Wayback captures for a run's sources.

    Only enabled when ``CRS_WAYBACK_AUTOLOOKUP`` is truthy (default off, so
    the research merge path performs no network calls). For up to ``limit``
    source URLs visited by the run that have no capture on record yet, run
    an availability lookup and persist hits to the KB. Every failure path
    is swallowed — this hook can never fail the merge.
    """
    if not _autolookup_enabled():
        return 0
    persisted = 0
    try:
        from ..graph import kb  # local import: keeps module importable without KB

        urls: List[str] = []
        for s in run.get("sources_visited") or []:
            u = (s.get("url") or "").strip()
            if u and u not in urls:
                urls.append(u)
        if not urls:
            return 0

        # Skip URLs that already carry a recorded capture.
        with kb.connect() as conn:
            known = {
                r["url"]
                for r in conn.execute(
                    "SELECT url FROM sources WHERE wayback_url IS NOT NULL"
                ).fetchall()
            }
        pending = [u for u in urls if u not in known][: max(1, int(limit))]

        for u in pending:
            try:
                result = wayback_availability(u)
                if result.get("archived"):
                    kb.set_source_archive(
                        u, result["wayback_url"], result.get("archived_at")
                    )
                    persisted += 1
            except Exception as exc:  # belt and braces: per-URL isolation
                logger.warning("wayback autolookup failed for %s: %s", u, exc)
        if persisted:
            logger.info("wayback autolookup: %d capture(s) recorded", persisted)
    except Exception as exc:
        logger.warning("wayback autolookup skipped: %s", exc)
    return persisted
