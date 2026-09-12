"""CRS-01 knowledge base — the persistent local graph store.

A single SQLite file (``backend/data/crs01.db`` by default, override with
``CRS_KB_PATH``) that accumulates every strain discovered by live research.
Nothing here is fabricated: rows only exist because a research run observed
them on the open web (or, later, because a human curator added them).

Design notes
------------
- Raw evidence is append-only: ``lineage_sources`` keeps one row per
  (child, parent, source_url) observation. Aggregates (``lineage_edges``,
  strain tiers) are *derived* and recomputed after each merge, so the data
  always speaks from the evidence.
- Agreement semantics (locked with the product owner 2026-08-19):
    green  = 2+ independent domains agree on the tuple  → COMMUNITY_CONSENSUS
    amber  = a single source asserts it                 → ANECDOTAL
    red    = sources assert different parent sets       → CONTRADICTED (kept)
  ``VERIFIED`` is reserved for human curation (``set_curated_tier``) and
  never assigned automatically.
- Every accessor opens a short-lived connection (SQLite open is cheap,
  WAL journal). This sidesteps cross-thread issues under FastAPI and lets
  tests point ``CRS_KB_PATH`` at a tmp file per test.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------

_DEFAULT_KB = Path(__file__).resolve().parents[2] / "data" / "crs01.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strains (
    slug            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    trust_tier      TEXT NOT NULL DEFAULT 'ANECDOTAL',
    confidence      REAL NOT NULL DEFAULT 0.5,
    origin          TEXT NOT NULL DEFAULT 'researched',
    summary         TEXT,
    summary_source_url   TEXT,
    summary_source_title TEXT,
    image_url       TEXT,
    thc_range       TEXT,
    strain_type     TEXT,
    breeder         TEXT,
    first_seen      INTEGER NOT NULL,
    last_researched INTEGER NOT NULL,
    curated_tier    TEXT,
    curated_note    TEXT,
    curated_at      INTEGER
);

CREATE TABLE IF NOT EXISTS sources (
    url         TEXT PRIMARY KEY,
    title       TEXT,
    engine      TEXT,
    fetched_at  INTEGER NOT NULL,
    wayback_url TEXT,
    archived_at INTEGER
);

CREATE TABLE IF NOT EXISTS lineage_sources (
    child_slug   TEXT NOT NULL,
    parent_slug  TEXT NOT NULL,
    source_url   TEXT NOT NULL,
    source_title TEXT,
    engine       TEXT,
    confidence   REAL NOT NULL DEFAULT 0.5,
    excerpt      TEXT,
    observed_at  INTEGER NOT NULL,
    PRIMARY KEY (child_slug, parent_slug, source_url)
);

CREATE TABLE IF NOT EXISTS claims (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    child_slug  TEXT NOT NULL,
    type        TEXT NOT NULL,
    value       TEXT NOT NULL,
    trust_tier  TEXT NOT NULL DEFAULT 'ANECDOTAL',
    confidence  REAL NOT NULL DEFAULT 0.5,
    source_url  TEXT,
    excerpt     TEXT,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS research_runs (
    id             TEXT PRIMARY KEY,
    query          TEXT NOT NULL,
    started_at     INTEGER,
    completed_at   INTEGER,
    providers_used TEXT,
    sources_count  INTEGER DEFAULT 0,
    claims_count   INTEGER DEFAULT 0,
    error          TEXT
);

CREATE INDEX IF NOT EXISTS ix_lineage_child ON lineage_sources (child_slug);
CREATE INDEX IF NOT EXISTS ix_lineage_parent ON lineage_sources (parent_slug);
CREATE INDEX IF NOT EXISTS ix_claims_child ON claims (child_slug);
"""


def db_path() -> Path:
    """Resolve the KB file location. Read per-call so tests can override."""
    return Path(os.environ.get("CRS_KB_PATH", str(_DEFAULT_KB)))


def _ensure_columns(
    conn: sqlite3.Connection, table: str, specs: Tuple[Tuple[str, str], ...]
) -> None:
    """Add any missing columns to a table (idempotent, append-only migration).

    ``CREATE TABLE IF NOT EXISTS`` can't alter a database that already exists
    (the production KB), so each column is added with a plain ``ALTER TABLE``
    when a PRAGMA probe shows it missing. A duplicate-column OperationalError
    means a prior or concurrent migration already added it and is swallowed;
    any other failure propagates instead of being silently masked.

    This is how raw-evidence tables grow without ever rewriting them:
    quarantine flags let curation exclude (never delete) observations, and
    the provenance columns (curated_*, summary_source_*, wayback_*) attach
    attributions to rows that predate them.
    """
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, col_type in specs:
        if name in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type}")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


@contextmanager
def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    _ensure_columns(
        conn, "lineage_sources", (("quarantined", "INTEGER NOT NULL DEFAULT 0"),)
    )
    _ensure_columns(conn, "claims", (("quarantined", "INTEGER NOT NULL DEFAULT 0"),))
    _ensure_columns(
        conn,
        "strains",
        (
            ("curated_tier", "TEXT"),
            ("curated_note", "TEXT"),
            ("curated_at", "INTEGER"),
            ("summary_source_url", "TEXT"),
            ("summary_source_title", "TEXT"),
        ),
    )
    _ensure_columns(
        conn,
        "sources",
        (
            ("wayback_url", "TEXT"),
            ("archived_at", "INTEGER"),
        ),
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def normalize_slug(name: str) -> str:
    return re.sub(r"\s+", "-", (name or "").strip().lower())


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return url


def _now() -> int:
    return int(time.time())


# ---------------------------------------------------------------------------
# Writers — raw evidence first, aggregates derived
# ---------------------------------------------------------------------------

def upsert_strain(
    conn: sqlite3.Connection,
    name: str,
    *,
    origin: str = "researched",
    summary: Optional[str] = None,
    image_url: Optional[str] = None,
    thc_range: Optional[str] = None,
    strain_type: Optional[str] = None,
    breeder: Optional[str] = None,
    last_researched: Optional[int] = None,
) -> str:
    """Insert or refresh a strain row. Never overwrites a non-null field
    with null (first observation wins for metadata we can't re-confirm)."""
    slug = normalize_slug(name)
    now = last_researched or _now()
    conn.execute(
        """
        INSERT INTO strains (slug, name, origin, summary, image_url,
                             thc_range, strain_type, breeder,
                             first_seen, last_researched)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            name            = excluded.name,
            summary         = COALESCE(excluded.summary,   strains.summary),
            image_url       = COALESCE(excluded.image_url, strains.image_url),
            thc_range       = COALESCE(excluded.thc_range, strains.thc_range),
            strain_type     = COALESCE(excluded.strain_type, strains.strain_type),
            breeder         = COALESCE(excluded.breeder,   strains.breeder),
            last_researched = excluded.last_researched
        """,
        (slug, name.strip(), origin, summary, image_url,
         thc_range, strain_type, breeder, now, now),
    )
    return slug


def upsert_source(
    conn: sqlite3.Connection, url: str, title: str = "", engine: str = ""
) -> None:
    if not url:
        return
    conn.execute(
        """
        INSERT INTO sources (url, title, engine, fetched_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            title = COALESCE(NULLIF(excluded.title, ''), sources.title),
            engine = COALESCE(NULLIF(excluded.engine, ''), sources.engine)
        """,
        (url, title or "", engine or "", _now()),
    )


def set_source_archive(
    url: str, wayback_url: str, archived_at: Optional[int]
) -> Optional[Dict[str, Any]]:
    """Record the Wayback capture observed for a source URL.

    Inserts the sources row when unknown (title/engine blank — we only
    know what archive.org told us, not what the page said) and stamps
    ``wayback_url`` + ``archived_at``. An existing row keeps its
    title/engine/``fetched_at``: an archive lookup is not a web fetch.
    No-op for a blank URL.
    """
    norm_url = (url or "").strip()
    if not norm_url:
        return None
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO sources (url, title, engine, fetched_at,
                                 wayback_url, archived_at)
            VALUES (?, '', '', ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
                wayback_url = excluded.wayback_url,
                archived_at = excluded.archived_at
            """,
            (norm_url, _now(), wayback_url or None, archived_at),
        )
        row = conn.execute(
            "SELECT url, title, engine, fetched_at, wayback_url, archived_at "
            "FROM sources WHERE url=?",
            (norm_url,),
        ).fetchone()
    return dict(row) if row else None


def record_lineage_observation(
    conn: sqlite3.Connection,
    child_name: str,
    parent_name: str,
    source_url: str,
    *,
    confidence: float = 0.5,
    source_title: str = "",
    engine: str = "",
    excerpt: str = "",
) -> None:
    """One raw (child ← parent) observation from one source URL."""
    child = normalize_slug(child_name)
    parent = normalize_slug(parent_name)
    if not child or not parent or child == parent:
        return
    conn.execute(
        """
        INSERT INTO lineage_sources (child_slug, parent_slug, source_url,
                                     source_title, engine, confidence,
                                     excerpt, observed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(child_slug, parent_slug, source_url) DO UPDATE SET
            confidence = MAX(lineage_sources.confidence, excluded.confidence),
            excerpt    = COALESCE(NULLIF(excluded.excerpt, ''), lineage_sources.excerpt)
        """,
        (child, parent, source_url, source_title, engine,
         confidence, excerpt, _now()),
    )


def record_claim(
    conn: sqlite3.Connection,
    child_slug: str,
    type_: str,
    value: str,
    *,
    trust_tier: str = "ANECDOTAL",
    confidence: float = 0.5,
    source_url: str = "",
    excerpt: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO claims (child_slug, type, value, trust_tier, confidence,
                            source_url, excerpt, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (child_slug, type_, value, trust_tier, confidence,
         source_url, excerpt, _now()),
    )


def record_run(
    conn: sqlite3.Connection,
    run_id: str,
    query: str,
    *,
    started_at: int = 0,
    completed_at: int = 0,
    providers_used: Optional[List[str]] = None,
    sources_count: int = 0,
    claims_count: int = 0,
    error: Optional[str] = None,
) -> None:
    conn.execute(
        """
        INSERT INTO research_runs (id, query, started_at, completed_at,
                                   providers_used, sources_count,
                                   claims_count, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            completed_at = excluded.completed_at,
            claims_count = excluded.claims_count,
            error        = excluded.error
        """,
        (run_id, query, started_at, completed_at,
         json.dumps(providers_used or []), sources_count, claims_count, error),
    )


# ---------------------------------------------------------------------------
# Aggregation — derive agreement, tiers, confidences from raw evidence
# ---------------------------------------------------------------------------

def recompute(conn: sqlite3.Connection) -> None:
    """Rebuild lineage_edges aggregates and strain tiers from raw evidence.

    Agreement per (child, parent):
      - 'disagreement'  → the child has 2+ distinct parent-SETS asserted
      - 'multi_source'  → 2+ independent domains assert this tuple
      - 'single_source' → one source only
    Tier for a strain (recomputed):
      CONTRADICTED if any disagreement, else COMMUNITY_CONSENSUS if any
      multi_source edge, else ANECDOTAL.
    """
    rows = conn.execute(
        "SELECT child_slug, parent_slug, source_url, confidence FROM lineage_sources "
        "WHERE NOT quarantined"
    ).fetchall()

    # parent-sets per child → disagreement detection
    child_parents: Dict[str, Set[str]] = {}
    for r in rows:
        child_parents.setdefault(r["child_slug"], set()).add(r["parent_slug"])

    # Evidence grouped per (child, parent)
    ev: Dict[Tuple[str, str], List[sqlite3.Row]] = {}
    for r in rows:
        ev.setdefault((r["child_slug"], r["parent_slug"]), []).append(r)

    # Determine disagreement: group observations by source? A single source
    # may assert multiple parents (a 3-way cross is ONE assertion of 3
    # parents, not 3 assertions of pairs). Since lineage_sources stores
    # pairwise rows, a 3-way cross from one URL yields 3 pairs. Treating
    # each pair-set as a distinct tuple would falsely flag every 3-way
    # cross as disagreement. Instead: an assertion = the set of parents a
    # single source URL gives a child. Disagreement = 2+ sources whose
    # parent sets for the same child are neither equal nor subsets.
    by_child_url: Dict[str, Dict[str, Set[str]]] = {}
    for r in rows:
        by_child_url.setdefault(r["child_slug"], {}).setdefault(
            r["source_url"], set()
        ).add(r["parent_slug"])
    disagree_children: Set[str] = set()
    for child, url_map in by_child_url.items():
        sets = [frozenset(v) for v in url_map.values()]
        for i in range(len(sets)):
            for j in range(i + 1, len(sets)):
                a, b = sets[i], sets[j]
                if a != b and not (a <= b) and not (b <= a):
                    disagree_children.add(child)
                    break

    conn.execute("DROP TABLE IF EXISTS lineage_edges_tmp")
    conn.execute(
        """
        CREATE TABLE lineage_edges_tmp (
            child_slug     TEXT NOT NULL,
            parent_slug    TEXT NOT NULL,
            source_count   INTEGER NOT NULL,
            domain_count   INTEGER NOT NULL,
            source_domains TEXT NOT NULL,
            sources        TEXT NOT NULL,
            avg_confidence REAL NOT NULL,
            agreement      TEXT NOT NULL,
            first_seen     INTEGER NOT NULL,
            PRIMARY KEY (child_slug, parent_slug)
        )
        """
    )
    for (child, parent), obs in ev.items():
        urls = sorted({o["source_url"] for o in obs if o["source_url"]})
        domains = sorted({_domain(u) for u in urls})
        avg_conf = sum(o["confidence"] for o in obs) / len(obs)
        if child in disagree_children:
            agreement = "disagreement"
        elif len(domains) >= 2:
            agreement = "multi_source"
        else:
            agreement = "single_source"
        conn.execute(
            "INSERT INTO lineage_edges_tmp VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (child, parent, len(urls), len(domains),
             json.dumps(domains), json.dumps(urls[:10]),
             round(avg_conf, 3), agreement,
             min(o["observed_at"] for o in
                 conn.execute(
                     "SELECT observed_at FROM lineage_sources WHERE child_slug=? AND parent_slug=?",
                     (child, parent),
                 ).fetchall()) or _now()),
        )
    conn.execute("DROP TABLE IF EXISTS lineage_edges")
    conn.execute("ALTER TABLE lineage_edges_tmp RENAME TO lineage_edges")

    # Recompute strain tiers + confidences. Children with lineage history
    # but NO surviving (non-quarantined) edges must also re-derive —
    # otherwise quarantining a child's last observation leaves a stale
    # CONTRADICTED/COMMUNITY_CONSENSUS verdict on it forever.
    history = conn.execute(
        "SELECT DISTINCT child_slug FROM lineage_sources"
    ).fetchall()
    tier_slugs = set(child_parents) | {r["child_slug"] for r in history}
    for slug in tier_slugs:
        edges = conn.execute(
            "SELECT agreement, avg_confidence FROM lineage_edges WHERE child_slug=?",
            (slug,),
        ).fetchall()
        curated = conn.execute(
            "SELECT curated_tier FROM strains WHERE slug=?", (slug,)
        ).fetchone()
        has_curated = curated is not None and curated["curated_tier"]
        if not edges:
            # Every observation quarantined (or none survived): no machine
            # verdict remains. Curated tiers stand untouched; anything else
            # falls back to the honest floor, ANECDOTAL.
            if not has_curated:
                conn.execute(
                    "UPDATE strains SET trust_tier='ANECDOTAL' WHERE slug=?",
                    (slug,),
                )
            continue
        if any(e["agreement"] == "disagreement" for e in edges):
            tier = "CONTRADICTED"
        elif any(e["agreement"] == "multi_source" for e in edges):
            tier = "COMMUNITY_CONSENSUS"
        else:
            tier = "ANECDOTAL"
        conf = sum(e["avg_confidence"] for e in edges) / len(edges)
        if has_curated:
            # Human curation outranks the machine verdict: a strain with a
            # curated tier keeps it across recomputes. VERIFIED in particular
            # is human-only per the locked 2026-08-19 agreement — it is never
            # assigned (or revoked) automatically. Confidence may still move.
            conn.execute(
                "UPDATE strains SET confidence=? WHERE slug=?",
                (round(conf, 3), slug),
            )
        else:
            conn.execute(
                "UPDATE strains SET trust_tier=?, confidence=? WHERE slug=?",
                (tier, round(conf, 3), slug),
            )


# ---------------------------------------------------------------------------
# High-level merge — ingest a completed research run
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Evidence-grounded summary fallback — mined from a run's raw evidence.
# VERBATIM only: the stored summary is a lead excerpt of one real snippet,
# never rewritten, never stitched from multiple sources. It only ever fills
# a NULL (LLM-provided or human text always wins) and always carries its
# source attribution.
# ---------------------------------------------------------------------------

# Mirrors the orchestrator's strong cannabis-only tokens. Kept local so the
# graph store does not depend on the ingestion package (no circular import
# risk, no heavy provider imports pulled into every kb reader).
_STRONG_CANNABIS_HINTS = (
    "cannabis", "marijuana", "hemp", "thc", "cbd", "cannabinoid",
    "terpene", "indica", "sativa", "ruderalis", "kush", "haze",
    "strain", "cultivar", "pot strain", "bud", "weed", "cannabies",
)

_SENTENCE_RE = re.compile(r"[^!?.]+[!?.]+")


def _looks_strongly_cannabis_text(text: str) -> bool:
    haystack = (text or "").lower()
    return any(h in haystack for h in _STRONG_CANNABIS_HINTS)


def _mentions_strain(haystack: str, name: str) -> bool:
    """Case-insensitive, hyphen/space-insensitive name containment.

    "AK-47" matches "AK 47", "ak-47", "ak47" — the same string written the
    ways the open web actually writes strain names.
    """
    token = re.sub(r"[\s\-_]+", " ", (name or "").lower()).strip()
    if not token:
        return False
    token_compact = re.sub(r"[\s\-_]+", "", token)
    norm = re.sub(r"[\s\-_]+", " ", (haystack or "").lower())
    compact = re.sub(r"[\s\-_]+", "", (haystack or "").lower())
    return token in norm or (len(token_compact) >= 2 and token_compact in compact)


def _lead_sentences(text: str, *, max_sentences: int = 3, max_chars: int = 400) -> str:
    """Verbatim lead of ``text``: up to ``max_sentences`` sentences within
    ``max_chars``, cut only at a sentence boundary (or a word boundary when
    the first sentence alone exceeds the budget) — never mid-word, never
    rewritten. Abbreviation-like fragments ("Mr.") fold into the sentence
    that follows so the cut lands on a real boundary."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return ""
    chunks = [m.group(0).strip() for m in _SENTENCE_RE.finditer(text)]
    merged: List[str] = []
    for chunk in chunks:
        if merged and len(merged[-1]) < 12:
            merged[-1] += " " + chunk
        else:
            merged.append(chunk)
    out: List[str] = []
    total = 0
    for s in merged:
        if len(out) >= max_sentences:
            break
        if out and total + 1 + len(s) > max_chars:
            break
        if not out and len(s) > max_chars:
            cut = s[:max_chars]
            sp = cut.rfind(" ")
            if sp > 0:
                cut = cut[:sp]
            return cut.strip()
        out.append(s)
        total += len(s) + (1 if total else 0)
    return " ".join(out).strip()


def _mine_summary_from_raw(
    raw_dir: Optional[str], query: str
) -> Optional[Dict[str, Optional[str]]]:
    """Best verbatim summary candidate from a run's raw provider evidence.

    Reads the JSON dumps the research orchestrator persisted under
    ``raw/<run_id>/*.json`` ({title, url, snippet, source, score,
    image_url}). A candidate must (a) mention the strain name
    (hyphen/space-insensitive) and (b) look cannabis-relevant (the same
    strong-token gate the orchestrator applies before extraction).
    Disambiguation stubs ("may refer to") never describe anything and are
    skipped. Scoring: Wikipedia-sourced rows first, then longer snippets.
    Missing/None ``raw_dir`` (e.g. in tests) yields None — no summary is
    invented.
    """
    if not raw_dir:
        return None
    directory = Path(raw_dir)
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return None
    best: Optional[Tuple[Tuple[int, int], str, str, str]] = None
    for path in paths:
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        snippet = (row.get("snippet") or "").strip()
        title = (row.get("title") or "").strip()
        url = (row.get("url") or "").strip()
        if not snippet or not url:
            continue
        haystack = f"{title} {snippet}"
        if not _mentions_strain(haystack, query):
            continue
        if not _looks_strongly_cannabis_text(haystack):
            continue
        if "disambiguation" in title.lower() or "may refer to" in snippet[:200].lower():
            continue
        summary = _lead_sentences(snippet)
        if not summary:
            continue
        score = (
            1 if (row.get("source") or "").lower().startswith("wikipedia") else 0,
            len(summary),
        )
        if best is None or score > best[0]:
            best = (score, url, title, summary)
    if best is None:
        return None
    return {
        "summary": best[3],
        "summary_source_url": best[1],
        "summary_source_title": best[2],
    }


def merge_research_run(run: Dict[str, Any]) -> Dict[str, Any]:
    """Persist a ResearchRun dict (from ingestion.research) into the KB.

    Expects keys: query, run_id, started_at, completed_at, providers_used,
    sources_visited, lineage_claims (each with child/parent_a/parent_b and
    optional extra_parents), and optional ``strain_meta`` mapping
    normalized slug → {summary, image_url, thc_range, strain_type, breeder}.
    Optional ``raw_dir`` points at the run's persisted provider evidence
    (raw/<run_id>/*.json); when the subject strain has no summary at all,
    one is mined VERBATIM from that evidence and stored with attribution
    (never overwriting an existing summary; missing raw_dir → no summary).

    Returns a summary dict {strains, edges, claims, sources} for logging.
    """
    claims = run.get("lineage_claims") or []
    meta = run.get("strain_meta") or {}
    counts = {"strains": 0, "edges": 0, "claims": 0, "sources": 0}

    with connect() as conn:
        for s in run.get("sources_visited") or []:
            upsert_source(conn, s.get("url", ""), s.get("title", ""), s.get("engine", ""))

        seen_strains: Set[str] = set()

        def _touch(name: str) -> None:
            slug = normalize_slug(name)
            if not slug or slug in seen_strains:
                return
            seen_strains.add(slug)
            m = meta.get(slug) or {}
            upsert_strain(
                conn,
                name,
                summary=m.get("summary"),
                image_url=m.get("image_url"),
                thc_range=m.get("thc_range"),
                strain_type=m.get("strain_type"),
                breeder=m.get("breeder"),
            )
            counts["strains"] += 1

        for c in claims:
            child = c.get("child") or run.get("query") or ""
            parents = [c.get("parent_a"), c.get("parent_b")]
            parents += c.get("extra_parents") or []
            parents = [p for p in parents if p]
            _touch(child)
            for p in parents:
                _touch(p)
                record_lineage_observation(
                    conn, child, p,
                    c.get("source_url", ""),
                    confidence=float(c.get("confidence", 0.5)),
                    source_title=c.get("source_title", ""),
                    engine=c.get("source_engine", ""),
                    excerpt=c.get("snippet_excerpt", ""),
                )
                counts["edges"] += 1
            # Keep a human-readable claim row per assertion, tied to the child.
            if parents:
                value = f"{child} = " + " × ".join(parents)
                dedup = conn.execute(
                    "SELECT 1 FROM claims WHERE child_slug=? AND value=? AND source_url=?",
                    (normalize_slug(child), value, c.get("source_url", "")),
                ).fetchone()
                if not dedup:
                    record_claim(
                        conn, normalize_slug(child), "LINEAGE", value,
                        confidence=float(c.get("confidence", 0.5)),
                        source_url=c.get("source_url", ""),
                        excerpt=c.get("snippet_excerpt", ""),
                    )
                    counts["claims"] += 1

        # Metadata-only strains (e.g. the subject when no parents were found)
        for name in [run.get("query") or ""]:
            if name:
                _touch(name)

        # Evidence-grounded summary fallback: when the subject strain has no
        # description at all, mine this run's raw evidence for one. Verbatim
        # only, attributed, and only ever fills a NULL/empty — an existing
        # summary (LLM-provided or previously mined) is never overwritten.
        subject_slug = normalize_slug(run.get("query") or "")
        if subject_slug:
            subject_row = conn.execute(
                "SELECT summary FROM strains WHERE slug=?", (subject_slug,)
            ).fetchone()
            if subject_row is not None and not (subject_row["summary"] or "").strip():
                mined = _mine_summary_from_raw(
                    run.get("raw_dir"), run.get("query") or ""
                )
                if mined:
                    conn.execute(
                        "UPDATE strains SET summary=?, summary_source_url=?, "
                        "summary_source_title=? "
                        "WHERE slug=? AND (summary IS NULL OR summary='')",
                        (mined["summary"], mined["summary_source_url"],
                         mined["summary_source_title"], subject_slug),
                    )

        record_run(
            conn,
            run.get("run_id") or f"run-{_now()}",
            run.get("query", ""),
            started_at=int(run.get("started_at") or 0),
            completed_at=int(run.get("completed_at") or 0),
            providers_used=run.get("providers_used") or [],
            sources_count=len(run.get("sources_visited") or []),
            claims_count=len(claims),
            error=run.get("error"),
        )
        counts["sources"] = len(run.get("sources_visited") or [])
        recompute(conn)
    return counts


# ---------------------------------------------------------------------------
# Readers — graph-shaped payloads for the API layer
# ---------------------------------------------------------------------------

def _strain_node(row: sqlite3.Row, relation: str = "related") -> Dict[str, Any]:
    props: Dict[str, Any] = {}
    if row["thc_range"]:
        props["thc_range"] = row["thc_range"]
    if row["strain_type"]:
        props["type"] = row["strain_type"]
    if row["breeder"]:
        props["breeder"] = row["breeder"]
    return {
        "id": row["slug"],
        "type": "Strain",
        "label": row["name"],
        "data": {
            "name": row["name"],
            "slug": row["slug"],
            "trust_tier": row["trust_tier"],
            "confidence": row["confidence"],
            "origin": row["origin"],
            # Human-curation provenance: when origin == 'curated' the tier
            # was set by a person, and the note/at stamp make that visible
            # (never asserted as machine-derived consensus).
            "curated_tier": row["curated_tier"],
            "curated_note": row["curated_note"],
            "curated_at": row["curated_at"],
            "relation": relation,
            "summary": row["summary"],
            # Provenance of a mined summary (verbatim lead of one source
            # snippet). NULL for LLM/human-written text — attribution exists
            # only when the words were mined from raw evidence.
            "summary_source_url": (
                row["summary_source_url"] if "summary_source_url" in row.keys() else None
            ),
            "summary_source_title": (
                row["summary_source_title"] if "summary_source_title" in row.keys() else None
            ),
            "image_url": row["image_url"],
            # Freshness — when this strain entered the KB and when research
            # last touched it. Absent timestamps stay absent (never faked).
            "first_seen": row["first_seen"],
            "last_researched": row["last_researched"],
            "props": props,
        },
    }


def _person_node(name: str, relation: str = "breeder") -> Dict[str, Any]:
    return {
        "id": f"person::{normalize_slug(name)}",
        "type": "Person",
        "label": name,
        "data": {
            "handle": name,
            "platform": "web",
            "display_name": name,
            "trust_tier": "ANECDOTAL",
            "confidence": 0.5,
            "origin": "researched",
            "relation": relation,
        },
    }


def _claim_node(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": f"claim::{row['id']}",
        "type": "Claim",
        "label": row["value"],
        "data": {
            "type": row["type"],
            "value": row["value"],
            "trust_tier": row["trust_tier"],
            "confidence": row["confidence"],
            "source_url": row["source_url"],
            # Page title from the joined sources row, when the URL is a real
            # http(s) source with a recorded title (provider pseudo-URLs like
            # tavily:// do not join — stays NULL, honestly).
            "source_title": row["source_title"] if "source_title" in row.keys() else None,
            "excerpt": row["excerpt"],
            # Archived copy of the source, when one was captured (LEFT-JOIN
            # from sources; NULL means exactly "no capture on record").
            "wayback_url": row["wayback_url"] if "wayback_url" in row.keys() else None,
            "origin": "researched",
            "relation": "claim",
        },
    }


def get_strain(name: str) -> Optional[Dict[str, Any]]:
    slug = normalize_slug(name)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM strains WHERE slug=? OR name=? COLLATE NOCASE",
            (slug, name.strip()),
        ).fetchone()
        return _strain_node(row) if row else None


def strain_exists(name: str) -> bool:
    return get_strain(name) is not None


def get_neighborhood(slug: str, depth: int) -> Dict[str, Any]:
    """k-hop subgraph around a strain, ReactFlow-shaped.

    Raises KeyError when the strain is unknown to the KB.
    Node data carries ``relation`` (parent/sibling/child/breeder/claim/
    related) so the polar wheel can bucket sectors by real semantics
    instead of name heuristics.
    """
    norm = normalize_slug(slug)
    with connect() as conn:
        subject = conn.execute("SELECT * FROM strains WHERE slug=?", (norm,)).fetchone()
        if subject is None:
            raise KeyError(slug)
        if depth < 0 or depth > 3:
            raise ValueError("depth must be 0..3")

        edges_rows = conn.execute(
            "SELECT * FROM lineage_edges"
        ).fetchall()

    # BFS over undirected lineage edges.
    adj: Dict[str, List[Tuple[str, str]]] = {}
    for e in edges_rows:
        adj.setdefault(e["child_slug"], []).append((e["parent_slug"], "up"))
        adj.setdefault(e["parent_slug"], []).append((e["child_slug"], "down"))

    visited: Set[str] = {norm}
    frontier = {norm}
    hop_of: Dict[str, int] = {norm: 0}
    for _ in range(depth):
        nxt: Set[str] = set()
        for nid in frontier:
            for other, _dir in adj.get(nid, []):
                if other not in visited:
                    visited.add(other)
                    hop_of[other] = hop_of[nid] + 1
                    nxt.add(other)
        frontier = nxt

    # Subject's direct parents + their other children (siblings).
    direct_parents = {e["parent_slug"] for e in edges_rows if e["child_slug"] == norm}
    siblings: Set[str] = set()
    for p in direct_parents:
        for e in edges_rows:
            if e["parent_slug"] == p and e["child_slug"] != norm:
                siblings.add(e["child_slug"])
    subject_children = {e["child_slug"] for e in edges_rows if e["parent_slug"] == norm}

    def relation_for(s: str) -> str:
        if s == norm:
            return "subject"
        if s in direct_parents:
            return "parent"
        if s in subject_children:
            return "child"
        if s in siblings:
            return "sibling"
        if hop_of.get(s, 99) < hop_of.get(norm, 0) + depth:
            # reached walking up → ancestor, else descendant/related
            return "ancestor" if s in _upstream(norm, edges_rows) else "descendant"
        return "related"

    nodes: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []

    with connect() as conn:
        rows = {
            r["slug"]: r
            for r in conn.execute(
                f"SELECT * FROM strains WHERE slug IN ({','.join('?' * len(visited))})",
                tuple(visited),
            ).fetchall()
        }
        for s in visited:
            if s in rows:
                nodes[s] = _strain_node(rows[s], relation_for(s))

        # Lineage edges among visited nodes, agreement-colored.
        for e in edges_rows:
            if e["child_slug"] in visited and e["parent_slug"] in visited:
                color = {
                    "multi_source": "green",
                    "single_source": "amber",
                    "disagreement": "red",
                }.get(e["agreement"], "amber")
                edges.append({
                    "id": f"e::{e['child_slug']}::{e['parent_slug']}",
                    "source": e["child_slug"],
                    "target": e["parent_slug"],
                    "type": "CHILD_OF",
                    "data": {
                        "confidence": e["avg_confidence"],
                        "source_count": e["source_count"],
                        "domain_count": e["domain_count"],
                        "source_domains": json.loads(e["source_domains"]),
                        "sources": json.loads(e["sources"]),
                        "agreement": e["agreement"],
                        "color": color,
                    },
                })

        # Derived breeder (Person) nodes for visited strains that have one.
        # Only included at depth ≥ 1 (they are one edge away).
        if depth > 0:
            person_ids: Set[str] = set()
            for s, row in rows.items():
                if s in visited and row["breeder"] and row["breeder"] not in person_ids:
                    person_ids.add(row["breeder"])
                    p = _person_node(row["breeder"])
                    nodes[p["id"]] = p
                    edges.append({
                        "id": f"e::{s}::bred_by::{p['id']}",
                        "source": s,
                        "target": p["id"],
                        "type": "BRED_BY",
                        "data": {"confidence": 0.5, "agreement": "single_source", "color": "amber"},
                    })

        # Claim nodes for visited strains (one per assertion row).
        # The subject's own claims are fetched first and never crowded out
        # by neighbors: a depth-2 hub like the cookies family can hold more
        # claims than the limit, which would otherwise truncate the subject's
        # parent accounts out of its own dossier.
        consulted_sources: Set[str] = set()
        if depth > 0:
            subject_claims = conn.execute(
                "SELECT c.*, s.wayback_url AS wayback_url, s.title AS source_title "
                "FROM claims c LEFT JOIN sources s ON s.url = c.source_url "
                "WHERE c.child_slug=? AND NOT c.quarantined "
                "ORDER BY c.created_at DESC LIMIT 24",
                (norm,),
            ).fetchall()
            claim_rows = list(subject_claims)
            remaining = 24 - len(claim_rows)
            if remaining > 0:
                others = list(visited - {norm})
                if others:
                    claim_rows += conn.execute(
                        f"SELECT c.*, s.wayback_url AS wayback_url, s.title AS source_title "
                        f"FROM claims c LEFT JOIN sources s ON s.url = c.source_url "
                        f"WHERE c.child_slug IN ({','.join('?' * len(others))}) "
                        "AND NOT c.quarantined "
                        f"ORDER BY c.created_at DESC LIMIT {int(remaining)}",
                        tuple(others),
                    ).fetchall()
            claim_child_by_id: Dict[int, str] = {}
            for cr in claim_rows:
                cn = _claim_node(cr)
                nodes[cn["id"]] = cn
                claim_child_by_id[cr["id"]] = cr["child_slug"]
                if cr["source_url"]:
                    consulted_sources.add(cr["source_url"])
                edges.append({
                    "id": f"e::claim::{cr['id']}",
                    "source": cn["id"],
                    "target": cr["child_slug"],
                    "type": "ABOUT_STRAIN",
                    "data": {
                        "confidence": cr["confidence"],
                        "agreement": "multi_source" if cr["trust_tier"] == "COMMUNITY_CONSENSUS" else "single_source",
                        "color": "green" if cr["trust_tier"] == "COMMUNITY_CONSENSUS" else "amber",
                    },
                })

    trust_dist: Dict[str, int] = {}
    for n in nodes.values():
        tier = (n.get("data") or {}).get("trust_tier", "ANECDOTAL")
        trust_dist[tier] = trust_dist.get(tier, 0) + 1

    return {
        "slug": norm,
        "depth": depth,
        "center_node_id": norm,
        "nodes": list(nodes.values()),
        "edges": edges,
        "stats": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "trust_distribution": trust_dist,
            # DISTINCT non-empty claim.source_url rows across the claims
            # included in this payload — how many places the dossier drew on.
            "sources_consulted": len(consulted_sources),
            "node_types": {
                "Strain": sum(1 for n in nodes.values() if n["type"] == "Strain"),
                "Person": sum(1 for n in nodes.values() if n["type"] == "Person"),
                "Claim": sum(1 for n in nodes.values() if n["type"] == "Claim"),
            },
        },
        "cached_at": _now(),
    }


def _upstream(slug: str, edge_rows: Iterable[Any]) -> Set[str]:
    """All ancestors of `slug` (transitive parents)."""
    parents: Dict[str, List[str]] = {}
    for e in edge_rows:
        parents.setdefault(e["child_slug"], []).append(e["parent_slug"])
    seen: Set[str] = set()
    stack = list(parents.get(slug, []))
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        stack.extend(parents.get(p, []))
    return seen


# ---------------------------------------------------------------------------
# Evidence drill-down — expose the raw rows behind every derived verdict.
# Conflicts are DERIVED here, never persisted: the locked evidence model
# (2026-08-19) keeps lineage_sources append-only and everything else derived.
# ---------------------------------------------------------------------------

def edge_evidence(child_slug: str) -> List[Dict[str, Any]]:
    """Full evidence behind every lineage edge of a child strain.

    One entry per parent: the edge aggregate (as recomputed into
    ``lineage_edges``) plus ``observations`` — ALL non-quarantined
    ``lineage_sources`` rows for that (child, parent), newest first.
    Parents that have raw observations but no ``strains`` row are included
    with the slug as display-name fallback. Unknown children yield [].
    """
    norm = normalize_slug(child_slug)
    with connect() as conn:
        edge_rows = {
            e["parent_slug"]: e
            for e in conn.execute(
                "SELECT * FROM lineage_edges WHERE child_slug=?", (norm,)
            ).fetchall()
        }
        obs = conn.execute(
            "SELECT ls.parent_slug, ls.source_url, ls.source_title, ls.engine, "
            "ls.confidence, ls.excerpt, ls.observed_at, s.wayback_url "
            "FROM lineage_sources ls "
            "LEFT JOIN sources s ON s.url = ls.source_url "
            "WHERE ls.child_slug=? AND NOT ls.quarantined "
            "ORDER BY ls.observed_at DESC, ls.source_url ASC",
            (norm,),
        ).fetchall()
        parent_slugs = sorted({o["parent_slug"] for o in obs} | set(edge_rows))
        parent_names: Dict[str, str] = {}
        if parent_slugs:
            for r in conn.execute(
                f"SELECT slug, name FROM strains WHERE slug IN ({','.join('?' * len(parent_slugs))})",
                tuple(parent_slugs),
            ):
                parent_names[r["slug"]] = r["name"]

    obs_by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for o in obs:
        obs_by_parent.setdefault(o["parent_slug"], []).append({
            "source_url": o["source_url"],
            "source_title": o["source_title"],
            "engine": o["engine"],
            "confidence": o["confidence"],
            "excerpt": o["excerpt"],
            "observed_at": o["observed_at"],
            # Archived copy when archive.org had one — NULL means exactly
            # "no capture on record", never "we didn't check".
            "wayback_url": o["wayback_url"],
        })

    edges_out: List[Dict[str, Any]] = []
    for parent in parent_slugs:
        observations = obs_by_parent.get(parent, [])
        edge = edge_rows.get(parent)
        if edge is not None:
            agreement: Optional[str] = edge["agreement"]
            source_count = edge["source_count"]
            domain_count = edge["domain_count"]
            source_domains = json.loads(edge["source_domains"])
            avg_confidence = edge["avg_confidence"]
        else:
            # Raw evidence without an aggregate yet (no recompute since the
            # merge) — derive the numbers on the fly, same formulas.
            urls = sorted({o["source_url"] for o in observations if o["source_url"]})
            domains = sorted({_domain(u) for u in urls})
            agreement = None
            source_count = len(urls)
            domain_count = len(domains)
            source_domains = domains
            avg_confidence = (
                round(sum(o["confidence"] for o in observations) / len(observations), 3)
                if observations else 0.0
            )
        edges_out.append({
            "parent": parent,
            "parent_name": parent_names.get(parent, parent),
            "agreement": agreement,
            "source_count": source_count,
            "domain_count": domain_count,
            "source_domains": source_domains,
            "avg_confidence": avg_confidence,
            "observations": observations,
        })

    # Most recently observed parent first; slug as deterministic tiebreaker.
    edges_out.sort(key=lambda e: (
        -(e["observations"][0]["observed_at"] if e["observations"] else 0),
        e["parent"],
    ))
    return edges_out


def strain_conflicts(child_slug: str) -> List[Dict[str, Any]]:
    """Derive conflicting parent-set assertions for a child — on the fly.

    Mirrors ``recompute()``'s disagreement rule exactly: an assertion is
    the set of parents a single source URL gives the child (a 3-way cross
    from one URL is ONE assertion); two assertions conflict when neither
    is equal to nor a subset of the other. Mutually-conflicting tuples are
    grouped into one conflict each. Unknown or clean children yield [].
    """
    norm = normalize_slug(child_slug)
    with connect() as conn:
        child_row = conn.execute(
            "SELECT name FROM strains WHERE slug=?", (norm,)
        ).fetchone()
        child_name = child_row["name"] if child_row else norm
        rows = conn.execute(
            "SELECT parent_slug, source_url, source_title, engine, observed_at "
            "FROM lineage_sources WHERE child_slug=? AND NOT quarantined",
            (norm,),
        ).fetchall()

        # url → assertion (set of parents) + source metadata.
        url_parents: Dict[str, Set[str]] = {}
        url_meta: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            url = r["source_url"]
            url_parents.setdefault(url, set()).add(r["parent_slug"])
            meta = url_meta.setdefault(
                url, {"title": "", "engine": "", "observed_at": 0}
            )
            if r["source_title"] and not meta["title"]:
                meta["title"] = r["source_title"]
            if r["engine"] and not meta["engine"]:
                meta["engine"] = r["engine"]
            meta["observed_at"] = max(meta["observed_at"], r["observed_at"])

        # Sources grouped by the tuple they assert.
        tuple_sources: Dict[frozenset, List[Dict[str, Any]]] = {}
        for url, parents in url_parents.items():
            meta = url_meta[url]
            tuple_sources.setdefault(frozenset(parents), []).append({
                "url": url,
                "title": meta["title"],
                "engine": meta["engine"],
                "observed_at": meta["observed_at"],
            })

        keys = sorted(tuple_sources, key=lambda fs: sorted(fs))

        # Union-find over tuples: connect those in direct conflict, then each
        # connected component (2+ members) is one enumerable conflict.
        parent_of = list(range(len(keys)))

        def _find(i: int) -> int:
            while parent_of[i] != i:
                parent_of[i] = parent_of[parent_of[i]]
                i = parent_of[i]
            return i

        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                a, b = keys[i], keys[j]
                # Same rule as recompute(): neither equal nor subset.
                if a != b and not (a <= b) and not (b <= a):
                    parent_of[_find(i)] = _find(j)
        components: Dict[int, List[int]] = {}
        for i in range(len(keys)):
            components.setdefault(_find(i), []).append(i)
        conflict_components = [
            sorted(c) for c in components.values() if len(c) >= 2
        ]

        # Display names for every parent in a conflicting tuple (slug fallback).
        wanted = sorted({
            p for c in conflict_components for i in c for p in keys[i]
        })
        name_map: Dict[str, str] = {}
        if wanted:
            for r in conn.execute(
                f"SELECT slug, name FROM strains WHERE slug IN ({','.join('?' * len(wanted))})",
                tuple(wanted),
            ):
                name_map[r["slug"]] = r["name"]

    conflicts: List[Dict[str, Any]] = []
    for comp in sorted(conflict_components, key=lambda c: sorted(keys[c[0]])):
        comp_keys = sorted((keys[i] for i in comp), key=lambda fs: sorted(fs))
        conflicts.append({
            "child": norm,
            "child_name": child_name,
            "summary": (
                f"{child_name} has {len(comp_keys)} conflicting "
                f"parent-strain assertions"
            ),
            "tuples": [
                {
                    "parents": sorted(fs),
                    "parent_names": [name_map.get(p, p) for p in sorted(fs)],
                    "sources": sorted(
                        tuple_sources[fs],
                        key=lambda s: (-s["observed_at"], s["url"]),
                    ),
                }
                for fs in comp_keys
            ],
        })
    return conflicts


# ---------------------------------------------------------------------------
# Human curation — the only path to VERIFIED.
# The locked evidence model (2026-08-19) reserves VERIFIED for human
# curation; these accessors are that human loop. Raw rows are never
# deleted: review stamps curated_* columns, quarantine flips a flag.
# ---------------------------------------------------------------------------

# Tiers a human may assign. CONTRADICTED is deliberately absent — it is a
# data-derived verdict (conflicting parent sets), not a curation choice.
CURATABLE_TIERS = ("VERIFIED", "COMMUNITY_CONSENSUS", "ANECDOTAL")


def _derive_strain_tier(conn: sqlite3.Connection, slug: str) -> str:
    """Same rule recompute() uses, readable outside it: CONTRADICTED if any
    disagreement edge, else COMMUNITY_CONSENSUS if any multi_source edge,
    else ANECDOTAL (also the fallback when no edges exist at all)."""
    edges = conn.execute(
        "SELECT agreement FROM lineage_edges WHERE child_slug=?", (slug,)
    ).fetchall()
    if any(e["agreement"] == "disagreement" for e in edges):
        return "CONTRADICTED"
    if any(e["agreement"] == "multi_source" for e in edges):
        return "COMMUNITY_CONSENSUS"
    return "ANECDOTAL"


def _strain_summary_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "slug": row["slug"],
        "name": row["name"],
        "trust_tier": row["trust_tier"],
        "confidence": row["confidence"],
        "origin": row["origin"],
        "curated_tier": row["curated_tier"],
        "curated_note": row["curated_note"],
        "curated_at": row["curated_at"],
    }


def set_curated_tier(
    slug: str, tier: Optional[str], note: str = ""
) -> Dict[str, Any]:
    """Apply (or clear) a human trust-tier verdict on a strain.

    tier in CURATABLE_TIERS → stamps curated_tier/note/curated_at and sets
    ``trust_tier`` + ``origin='curated'``. tier None → clears the curated
    columns, returns ``origin`` to 'researched' and re-derives trust_tier
    from the current edges. Raises KeyError for an unknown strain and
    ValueError for a tier a human may not assign.
    """
    if tier is not None and tier not in CURATABLE_TIERS:
        raise ValueError(
            f"tier must be one of {', '.join(CURATABLE_TIERS)} or null "
            f"(got {tier!r}); CONTRADICTED is data-derived and not assignable"
        )
    norm = normalize_slug(slug)
    with connect() as conn:
        row = conn.execute("SELECT slug FROM strains WHERE slug=?", (norm,)).fetchone()
        if row is None:
            raise KeyError(norm)
        if tier is None:
            # Clearing the review hands the verdict back to the evidence.
            conn.execute(
                "UPDATE strains SET curated_tier=NULL, curated_note=NULL, "
                "curated_at=NULL, origin='researched' WHERE slug=?",
                (norm,),
            )
            conn.execute(
                "UPDATE strains SET trust_tier=? WHERE slug=?",
                (_derive_strain_tier(conn, norm), norm),
            )
        else:
            conn.execute(
                "UPDATE strains SET curated_tier=?, curated_note=NULLIF(?, ''), "
                "curated_at=?, trust_tier=?, origin='curated' WHERE slug=?",
                (tier, note.strip(), _now(), tier, norm),
            )
        return _strain_summary_row(
            conn.execute("SELECT * FROM strains WHERE slug=?", (norm,)).fetchone()
        )


def set_observation_quarantined(
    child_slug: str, parent_slug: str, source_url: str, quarantined: bool = True
) -> Dict[str, Any]:
    """Flip the ``quarantined`` flag on one raw observation and the LINEAGE
    claim row from the same source, then re-derive everything.

    The lineage_sources row is never deleted (append-only); quarantine just
    hides it from every aggregate. Raises KeyError when no observation row
    matches. Returns the updated strain state plus what remains of that edge.
    """
    norm_child = normalize_slug(child_slug)
    norm_parent = normalize_slug(parent_slug)
    flag = 1 if quarantined else 0
    with connect() as conn:
        cur = conn.execute(
            "UPDATE lineage_sources SET quarantined=? "
            "WHERE child_slug=? AND parent_slug=? AND source_url=?",
            (flag, norm_child, norm_parent, source_url),
        )
        if cur.rowcount == 0:
            raise KeyError(
                f"no observation row for ({norm_child}, {norm_parent}, {source_url})"
            )
        # Matching LINEAGE claims from the same URL follow the observation.
        conn.execute(
            "UPDATE claims SET quarantined=? "
            "WHERE child_slug=? AND type='LINEAGE' AND source_url=?",
            (flag, norm_child, source_url),
        )
        recompute(conn)
        strain = conn.execute(
            "SELECT * FROM strains WHERE slug=?", (norm_child,)
        ).fetchone()
        remaining = conn.execute(
            "SELECT COUNT(*) AS c FROM lineage_sources "
            "WHERE child_slug=? AND parent_slug=? AND NOT quarantined",
            (norm_child, norm_parent),
        ).fetchone()["c"]
        edge = conn.execute(
            "SELECT agreement, source_count, domain_count FROM lineage_edges "
            "WHERE child_slug=? AND parent_slug=?",
            (norm_child, norm_parent),
        ).fetchone()
    return {
        **(_strain_summary_row(strain) if strain else {
            "slug": norm_child, "name": norm_child, "trust_tier": None,
            "confidence": None, "origin": None,
            "curated_tier": None, "curated_note": None, "curated_at": None,
        }),
        "quarantined": quarantined,
        "edge": {
            "parent": norm_parent,
            "remaining_observations": remaining,
            "agreement": edge["agreement"] if edge else None,
            "source_count": edge["source_count"] if edge else 0,
            "domain_count": edge["domain_count"] if edge else 0,
        },
    }


# ---------------------------------------------------------------------------
# Research run history — the research_runs table is written on every merge;
# these accessors make it readable again.
# ---------------------------------------------------------------------------

def list_research_runs(limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    """Recent research runs, newest first, with providers_used parsed."""
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    with connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM research_runs"
        ).fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM research_runs ORDER BY started_at DESC, id ASC "
            "LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "runs": [
            {
                "id": r["id"],
                "query": r["query"],
                "started_at": r["started_at"],
                "completed_at": r["completed_at"],
                "providers_used": json.loads(r["providers_used"] or "[]"),
                "sources_count": r["sources_count"],
                "claims_count": r["claims_count"],
                "error": r["error"],
            }
            for r in rows
        ],
    }


def get_research_run(run_id: str) -> Optional[Dict[str, Any]]:
    """One research run row by id, or None."""
    with connect() as conn:
        r = conn.execute(
            "SELECT * FROM research_runs WHERE id=?", (run_id,)
        ).fetchone()
    if r is None:
        return None
    return {
        "id": r["id"],
        "query": r["query"],
        "started_at": r["started_at"],
        "completed_at": r["completed_at"],
        "providers_used": json.loads(r["providers_used"] or "[]"),
        "sources_count": r["sources_count"],
        "claims_count": r["claims_count"],
        "error": r["error"],
    }


def list_strains(limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM strains").fetchone()["c"]
        rows = conn.execute(
            "SELECT * FROM strains ORDER BY confidence DESC, name ASC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": r["slug"],
                "name": r["name"],
                "slug": r["slug"],
                "trust_tier": r["trust_tier"],
                "confidence": r["confidence"],
                "origin": r["origin"],
                "image_url": r["image_url"],
                "summary": r["summary"],
                "first_seen": r["first_seen"],
                "last_researched": r["last_researched"],
                "props": {
                    k: v
                    for k, v in {
                        "thc_range": r["thc_range"],
                        "type": r["strain_type"],
                        "breeder": r["breeder"],
                    }.items()
                    if v
                },
            }
            for r in rows
        ],
    }


def list_sources(limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """Sources rows (the archive read path), newest fetched first.

    ``wayback_url``/``archived_at`` are NULL when no capture is on record —
    exactly that, never an implied error.
    """
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM sources").fetchone()["c"]
        rows = conn.execute(
            "SELECT url, title, engine, fetched_at, wayback_url, archived_at "
            "FROM sources ORDER BY fetched_at DESC, url ASC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "sources": [
            {
                "url": r["url"],
                "title": r["title"],
                "engine": r["engine"],
                "fetched_at": r["fetched_at"],
                "wayback_url": r["wayback_url"],
                "archived_at": r["archived_at"],
            }
            for r in rows
        ],
    }


def search_strains(q: str, limit: int = 8) -> Dict[str, Any]:
    """Substring search with the 3/2/1 scoring used by the old catalog."""
    needle = (q or "").strip().lower()
    if not needle:
        return {"query": q, "matches": []}
    with connect() as conn:
        rows = conn.execute("SELECT * FROM strains").fetchall()
    scored: List[Tuple[int, Dict[str, Any]]] = []
    for r in rows:
        name = (r["name"] or "").lower()
        slug = r["slug"]
        score = 0
        if needle == name or needle == slug:
            score = 3
        elif name.startswith(needle) or slug.startswith(needle):
            score = 2
        elif needle in name or needle in slug:
            score = 1
        if score > 0:
            scored.append((score, {
                "id": r["slug"],
                "name": r["name"],
                "slug": r["slug"],
                "trust_tier": r["trust_tier"],
                "confidence": r["confidence"],
                "origin": r["origin"],
            }))
    scored.sort(key=lambda t: (-t[0], -t[1]["confidence"], t[1]["name"]))
    return {"query": q, "matches": [m for _, m in scored[:limit]]}


def suggest_similar(q: str, limit: int = 4) -> List[Dict[str, Any]]:
    """'Did you mean…' — fuzzy match against known strain names."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM strains LIMIT 500").fetchall()
    names = {r["name"]: r for r in rows}
    close = difflib.get_close_matches(
        (q or "").strip(), list(names.keys()), n=limit, cutoff=0.5
    )
    return [
        {
            "slug": names[n]["slug"],
            "name": names[n]["name"],
            "confidence": names[n]["confidence"],
        }
        for n in close
    ]


def stats() -> Dict[str, Any]:
    with connect() as conn:
        strains = conn.execute("SELECT trust_tier, slug FROM strains").fetchall()
        n_edges = conn.execute("SELECT COUNT(*) AS c FROM lineage_edges").fetchone()["c"]
        n_sources = conn.execute("SELECT COUNT(*) AS c FROM sources").fetchone()["c"]
        n_claims = conn.execute(
            "SELECT COUNT(*) AS c FROM claims WHERE NOT quarantined"
        ).fetchone()["c"]
        runs = conn.execute("SELECT COUNT(*) AS c FROM research_runs").fetchone()["c"]
    tier_counts: Dict[str, int] = {}
    for r in strains:
        tier_counts[r["trust_tier"]] = tier_counts.get(r["trust_tier"], 0) + 1
    return {
        # Strain + Claim nodes are both graph nodes; Person nodes are derived
        # on the fly and not counted here.
        "total_nodes": len(strains) + n_claims,
        "total_edges": n_edges,
        "total_sources": n_sources,
        "total_claims": n_claims,
        "research_runs": runs,
        "node_types": {"Strain": len(strains), "Person": 0, "Claim": n_claims},
        "trust_distribution": tier_counts,
    }
