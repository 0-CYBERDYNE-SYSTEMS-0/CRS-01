"""CRS-01 knowledge base — the persistent local graph store.

A single SQLite file (``backend/data/crs01.db`` by default, override with
``CRS_KB_PATH``) that accumulates every strain discovered by live research.
Nothing here is fabricated: rows only exist because a research run observed
them on the open web (or, later, because a human curator added them).

Design notes
------------
- Raw evidence is append-only: ``lineage_sources`` keeps one row per
  (child, parent, source_url) observation. Aggregates (``lineage_edges``,
  strain tiers) are *derived* and recomputed after each merge, so the
  data always speaks from the evidence.
- Unresolved parents are gated, not materialized: an observation naming a
  parent strain the KB has never seen is recorded but flagged
  ``quarantine_reason='UNRESOLVED_PARENT'`` — it enters no aggregate and
  no strain node is created for the name. The gate clears when the parent
  becomes a real KB strain through its own research, or a curator resolves
  it (``resolve_parent``). A human quarantine/restore decision
  (``HUMAN_APPROVED``) always supersedes the mechanical gate.
- Strain aliases live on ``strains.aliases_json`` (normalized slug strings).
  Merge and ``recompute()`` resolve an observed name against slug-or-alias
  so ``GSC`` attaches to ``girl-scout-cookies`` instead of spawning a
  sibling node. Historical ``lineage_sources.parent_slug`` values are
  never rewritten (append-only); the alias map is applied at derive time.
  ``resolve_parent(..., alias_of=canonical)`` is the curator path that
  records a pending name as an alias rather than materializing it.
- Parent ``role`` on ``lineage_sources`` is ``female`` / ``male`` /
  ``parent`` / NULL. NULL means unknown. Role is stored only when the
  extractor saw an explicit cue in the excerpt — never inferred from
  parent order. Derived ``lineage_edges.role`` is set only when
  non-quarantined sources for that (child, parent) agree.
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

# quarantine_reason values on lineage_sources / claims (NULL = a plain human
# quarantine — the pre-gate meaning of quarantined=1).
UNRESOLVED_PARENT = "UNRESOLVED_PARENT"   # mechanical gate, active
HUMAN_APPROVED = "HUMAN_APPROVED"         # human cleared it; never re-gated

# Explicit parent-role values stored on lineage_sources / lineage_edges.
# NULL on the row means unknown; "parent" is an explicit unsexed role.
PARENT_ROLES = ("female", "male", "parent")
_SEXED_ROLES = frozenset({"female", "male"})

# High-precision aliases seeded onto an *existing* canonical strain.
# Ambiguous family names (cookies, og, chem) are deliberately absent —
# those stay ingest-time or curator-only.
_SAFE_ALIASES: Dict[str, str] = {
    "gsc": "girl-scout-cookies",
    "gs-cookies": "girl-scout-cookies",
    "girl-scout-cookie": "girl-scout-cookies",
    "chem-dog": "chemdawg",
    "chem-d": "chemdawg",
    "chemdog": "chemdawg",
    "chemdawg-d": "chemdawg",
    "sour-d": "sour-diesel",
    "sour-deisel": "sour-diesel",
    "gdp": "granddaddy-purple",
    "grandaddy-purple": "granddaddy-purple",
    "grand-daddy-purple": "granddaddy-purple",
    "granddaddy-purps": "granddaddy-purple",
    "northen-lights": "northern-lights",
    "northern-light": "northern-lights",
    "ak47": "ak-47",
    "thin-mint": "thin-mints",
}

def _slug_display_name(slug: str) -> str:
    """Default display name for a slug with no strains row yet — a best-effort
    title-case of the slug's parts. Curators pass a real name on resolve."""
    return " ".join(part.capitalize() for part in (slug or "").split("-") if part)

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
    error          TEXT,
    llm_calls          INTEGER DEFAULT 0,
    prompt_tokens      INTEGER DEFAULT 0,
    completion_tokens  INTEGER DEFAULT 0,
    total_tokens       INTEGER DEFAULT 0,
    llm_model          TEXT
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
        conn,
        "lineage_sources",
        (
            ("quarantined", "INTEGER NOT NULL DEFAULT 0"),
            ("quarantine_reason", "TEXT"),
            ("role", "TEXT"),
        ),
    )
    _ensure_columns(
        conn,
        "claims",
        (
            ("quarantined", "INTEGER NOT NULL DEFAULT 0"),
            ("quarantine_reason", "TEXT"),
        ),
    )
    _ensure_columns(
        conn,
        "strains",
        (
            ("curated_tier", "TEXT"),
            ("curated_note", "TEXT"),
            ("curated_at", "INTEGER"),
            ("summary_source_url", "TEXT"),
            ("summary_source_title", "TEXT"),
            ("aliases_json", "TEXT"),
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
    _ensure_columns(
        conn,
        "research_runs",
        (
            ("llm_calls", "INTEGER DEFAULT 0"),
            ("prompt_tokens", "INTEGER DEFAULT 0"),
            ("completion_tokens", "INTEGER DEFAULT 0"),
            ("total_tokens", "INTEGER DEFAULT 0"),
            ("llm_model", "TEXT"),
        ),
    )
    if _ensure_safe_aliases(conn):
        # Newly attached aliases must flow into derived edges and release
        # any gated observations that now name a known strain. Do it here
        # so a fresh clone's seed KB collapses GSC/Chem D without a merge.
        _run_parent_gate(conn, set())
        recompute(conn)
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


def _norm_role(role: Optional[str]) -> Optional[str]:
    """Return a stored role value, or None if missing/invalid."""
    if not role:
        return None
    r = str(role).strip().lower()
    return r if r in PARENT_ROLES else None


def _parse_aliases_json(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        vals = raw
    else:
        try:
            vals = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
    out: List[str] = []
    for v in vals:
        s = normalize_slug(str(v))
        if s:
            out.append(s)
    return sorted(set(out))


def _dump_aliases(aliases: Iterable[str]) -> str:
    return json.dumps(_parse_aliases_json(list(aliases)))


def _alias_map(conn: sqlite3.Connection) -> Dict[str, str]:
    """Observed slug → canonical strain slug.

    Canonical slugs map to themselves. An alias maps to its owning strain.
    First owner wins if two rows somehow share an alias (writes prevent that).
    """
    mapping: Dict[str, str] = {}
    for r in conn.execute("SELECT slug, aliases_json FROM strains"):
        mapping[r["slug"]] = r["slug"]
        raw = r["aliases_json"] if "aliases_json" in r.keys() else None
        for alias in _parse_aliases_json(raw):
            mapping.setdefault(alias, r["slug"])
    return mapping


def lookup_canonical_slug(conn: sqlite3.Connection, name: str) -> Optional[str]:
    """Canonical slug if ``name`` is a known strain or an alias of one."""
    slug = normalize_slug(name)
    if not slug:
        return None
    return _alias_map(conn).get(slug)


def _canon_slug(amap: Dict[str, str], slug: str) -> str:
    return amap.get(slug, slug)


def _ensure_safe_aliases(conn: sqlite3.Connection) -> bool:
    """Attach high-precision aliases to canonical strains that already exist.

    Never creates a strain, never claims an alias that is another strain's
    slug, never overwrites curator-owned aliases. Returns True when any
    row actually changed so the caller can recompute.
    """
    existing = {
        r["slug"]: r["aliases_json"] if "aliases_json" in r.keys() else None
        for r in conn.execute("SELECT slug, aliases_json FROM strains")
    }
    if not existing:
        return False
    changed = False
    for alias, canon in _SAFE_ALIASES.items():
        if canon not in existing:
            continue
        if alias in existing and alias != canon:
            continue
        current = _parse_aliases_json(existing.get(canon))
        if alias in current or alias == canon:
            continue
        current.append(alias)
        dumped = _dump_aliases(current)
        conn.execute(
            "UPDATE strains SET aliases_json=? WHERE slug=?", (dumped, canon)
        )
        existing[canon] = dumped
        changed = True
    return changed


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
    """Insert or refresh a strain row. Fill-only-if-null: the FIRST non-null
    value for each metadata field (summary, image_url, thc_range, strain_type,
    breeder) wins and later research runs never overwrite it — automated
    merges can fill gaps but cannot churn existing metadata. Nulls never
    clobber in either direction; only the empty→filled transition happens.

    ``last_researched`` always refreshes (freshness is not metadata), and
    ``name`` refreshes so the stored spelling tracks the latest canonical
    form of the same slug."""
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
            summary         = COALESCE(strains.summary,   excluded.summary),
            image_url       = COALESCE(strains.image_url, excluded.image_url),
            thc_range       = COALESCE(strains.thc_range, excluded.thc_range),
            strain_type     = COALESCE(strains.strain_type, excluded.strain_type),
            breeder         = COALESCE(strains.breeder,   excluded.breeder),
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
    role: Optional[str] = None,
) -> None:
    """One raw (child ← parent) observation from one source URL.

    Child/parent names resolve through slug-or-alias so a known alias
    writes against the canonical strain. Unknown names keep their observed
    slug (the unresolved-parent gate then holds them). Role is stored only
    when explicit; ON CONFLICT never overwrites a previously recorded role.
    """
    child = lookup_canonical_slug(conn, child_name) or normalize_slug(child_name)
    parent = lookup_canonical_slug(conn, parent_name) or normalize_slug(parent_name)
    if not child or not parent or child == parent:
        return
    stored_role = _norm_role(role)
    conn.execute(
        """
        INSERT INTO lineage_sources (child_slug, parent_slug, source_url,
                                     source_title, engine, confidence,
                                     excerpt, observed_at, role)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(child_slug, parent_slug, source_url) DO UPDATE SET
            confidence = MAX(lineage_sources.confidence, excluded.confidence),
            excerpt    = COALESCE(NULLIF(excluded.excerpt, ''), lineage_sources.excerpt),
            role       = COALESCE(lineage_sources.role, excluded.role)
        """,
        (child, parent, source_url, source_title, engine,
         confidence, excerpt, _now(), stored_role),
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
    usage: Optional[Dict[str, Any]] = None,
) -> None:
    """Insert/refresh a research_runs row. ``usage`` is the orchestrator's
    accumulated LLM spend ({calls, model, prompt_tokens, completion_tokens,
    total_tokens}); stored so spend survives even for runs that errored."""
    usage = usage if isinstance(usage, dict) else {}
    conn.execute(
        """
        INSERT INTO research_runs (id, query, started_at, completed_at,
                                   providers_used, sources_count,
                                   claims_count, error,
                                   llm_calls, prompt_tokens,
                                   completion_tokens, total_tokens,
                                   llm_model)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            completed_at = excluded.completed_at,
            claims_count = excluded.claims_count,
            error        = excluded.error,
            llm_calls         = excluded.llm_calls,
            prompt_tokens     = excluded.prompt_tokens,
            completion_tokens = excluded.completion_tokens,
            total_tokens      = excluded.total_tokens,
            llm_model         = excluded.llm_model
        """,
        (run_id, query, started_at, completed_at,
         json.dumps(providers_used or []), sources_count, claims_count, error,
         int(usage.get("calls", 0) or 0),
         int(usage.get("prompt_tokens", 0) or 0),
         int(usage.get("completion_tokens", 0) or 0),
         int(usage.get("total_tokens", 0) or 0),
         usage.get("model") or None),
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
    amap = _alias_map(conn)
    rows = conn.execute(
        "SELECT child_slug, parent_slug, source_url, confidence, role "
        "FROM lineage_sources WHERE NOT quarantined"
    ).fetchall()

    def _c(slug: str) -> str:
        return _canon_slug(amap, slug)

    # parent-sets per child → disagreement detection
    child_parents: Dict[str, Set[str]] = {}
    for r in rows:
        child_parents.setdefault(_c(r["child_slug"]), set()).add(_c(r["parent_slug"]))

    # Evidence grouped per canonical (child, parent)
    ev: Dict[Tuple[str, str], List[sqlite3.Row]] = {}
    for r in rows:
        ev.setdefault((_c(r["child_slug"]), _c(r["parent_slug"])), []).append(r)

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
        by_child_url.setdefault(_c(r["child_slug"]), {}).setdefault(
            r["source_url"], set()
        ).add(_c(r["parent_slug"]))
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
            role           TEXT,
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
        sexed = {
            _norm_role(o["role"] if "role" in o.keys() else None)
            for o in obs
        } & _SEXED_ROLES
        edge_role = next(iter(sexed)) if len(sexed) == 1 else None
        # first_seen: min observed_at across raw rows that map to this
        # canonical pair (including historical alias spellings).
        raw_slugs = [
            (o["child_slug"], o["parent_slug"]) for o in obs
        ]
        first_seen = _now()
        for cslug, pslug in set(raw_slugs):
            seen_rows = conn.execute(
                "SELECT MIN(observed_at) AS t FROM lineage_sources "
                "WHERE child_slug=? AND parent_slug=?",
                (cslug, pslug),
            ).fetchone()
            if seen_rows and seen_rows["t"] is not None:
                first_seen = min(first_seen, int(seen_rows["t"]))
        conn.execute(
            "INSERT INTO lineage_edges_tmp VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (child, parent, len(urls), len(domains),
             json.dumps(domains), json.dumps(urls[:10]),
             round(avg_conf, 3), agreement, first_seen, edge_role),
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
    tier_slugs = set(child_parents) | {_c(r["child_slug"]) for r in history}
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

    Unresolved-parent gate: a parent name the strains table has never seen
    is NOT materialized; its observations are recorded but gated
    (quarantine_reason='UNRESOLVED_PARENT') until the name becomes a real
    KB strain through its own research or a curator resolves it.

    Returns a summary dict {strains, edges, claims, sources} for logging.
    """
    claims = run.get("lineage_claims") or []
    meta = run.get("strain_meta") or {}
    counts = {"strains": 0, "edges": 0, "claims": 0, "sources": 0}

    with connect() as conn:
        for s in run.get("sources_visited") or []:
            upsert_source(conn, s.get("url", ""), s.get("title", ""), s.get("engine", ""))

        seen_strains: Set[str] = set()
        # (child, parent, url) observations written by THIS merge whose
        # parent had no strains row — handed to the unresolved-parent gate.
        fresh_unknown: Set[Tuple[str, str, str]] = set()

        def _touch(name: str) -> None:
            slug = normalize_slug(name)
            if not slug:
                return
            canon = lookup_canonical_slug(conn, slug)
            if canon:
                if canon in seen_strains:
                    return
                seen_strains.add(canon)
                row = conn.execute(
                    "SELECT name FROM strains WHERE slug=?", (canon,)
                ).fetchone()
                display = row["name"] if row else name
                m = meta.get(canon) or meta.get(slug) or {}
                upsert_strain(
                    conn,
                    display,
                    summary=m.get("summary"),
                    image_url=m.get("image_url"),
                    thc_range=m.get("thc_range"),
                    strain_type=m.get("strain_type"),
                    breeder=m.get("breeder"),
                )
                counts["strains"] += 1
                return
            if slug in seen_strains:
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

        def _known(name: str) -> bool:
            return lookup_canonical_slug(conn, name) is not None

        for c in claims:
            child = c.get("child") or run.get("query") or ""
            parents = [c.get("parent_a"), c.get("parent_b")]
            parents += c.get("extra_parents") or []
            parents = [p for p in parents if p]
            roles = c.get("parent_roles") or {}
            _touch(child)
            for p in parents:
                known = _known(p)
                if known:
                    # Known parents (slug or alias) refresh like before.
                    # Unknown parents are NOT materialized — their
                    # observations land in the gate until the name proves
                    # real (see _run_parent_gate).
                    _touch(p)
                role = roles.get(p) or roles.get(normalize_slug(p))
                record_lineage_observation(
                    conn, child, p,
                    c.get("source_url", ""),
                    confidence=float(c.get("confidence", 0.5)),
                    source_title=c.get("source_title", ""),
                    engine=c.get("source_engine", ""),
                    excerpt=c.get("snippet_excerpt", ""),
                    role=role,
                )
                if not known:
                    written_child = (
                        lookup_canonical_slug(conn, child) or normalize_slug(child)
                    )
                    written_parent = normalize_slug(p)
                    fresh_unknown.add((
                        written_child, written_parent,
                        c.get("source_url", ""),
                    ))
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

        # Unresolved-parent gate: hold fresh unknown-parent observations
        # out of every aggregate, then release any whose parent has (in this
        # or an earlier merge) become a real KB strain. Spec §3.1.
        _run_parent_gate(conn, fresh_unknown)

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
            usage=run.get("llm_usage"),
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
    aliases = _parse_aliases_json(
        row["aliases_json"] if "aliases_json" in row.keys() else None
    )
    return {
        "id": row["slug"],
        "type": "Strain",
        "label": row["name"],
        "data": {
            "name": row["name"],
            "slug": row["slug"],
            "aliases": aliases,
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
        canon = lookup_canonical_slug(conn, slug)
        if canon:
            row = conn.execute(
                "SELECT * FROM strains WHERE slug=?", (canon,)
            ).fetchone()
        else:
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
        canon = lookup_canonical_slug(conn, norm)
        if canon:
            norm = canon
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
                role = e["role"] if "role" in e.keys() else None
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
                        "role": role,
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
    requested = normalize_slug(child_slug)
    with connect() as conn:
        amap = _alias_map(conn)
        norm = _canon_slug(amap, requested)
        # Observations may still name the child (or a parent) by an alias
        # slug; gather every raw spelling that maps to this canonical child.
        child_spellings = {norm} | {
            s for s, c in amap.items() if c == norm
        }
        edge_rows = {
            e["parent_slug"]: e
            for e in conn.execute(
                "SELECT * FROM lineage_edges WHERE child_slug=?", (norm,)
            ).fetchall()
        }
        placeholders = ",".join("?" * len(child_spellings))
        obs = conn.execute(
            "SELECT ls.parent_slug, ls.source_url, ls.source_title, ls.engine, "
            "ls.confidence, ls.excerpt, ls.observed_at, ls.role, s.wayback_url "
            "FROM lineage_sources ls "
            "LEFT JOIN sources s ON s.url = ls.source_url "
            f"WHERE ls.child_slug IN ({placeholders}) AND NOT ls.quarantined "
            "ORDER BY ls.observed_at DESC, ls.source_url ASC",
            tuple(child_spellings),
        ).fetchall()
        parent_slugs = sorted(
            {_canon_slug(amap, o["parent_slug"]) for o in obs} | set(edge_rows)
        )
        parent_names: Dict[str, str] = {}
        if parent_slugs:
            for r in conn.execute(
                f"SELECT slug, name FROM strains WHERE slug IN ({','.join('?' * len(parent_slugs))})",
                tuple(parent_slugs),
            ):
                parent_names[r["slug"]] = r["name"]

    obs_by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for o in obs:
        parent = _canon_slug(amap, o["parent_slug"])
        obs_by_parent.setdefault(parent, []).append({
            "source_url": o["source_url"],
            "source_title": o["source_title"],
            "engine": o["engine"],
            "confidence": o["confidence"],
            "excerpt": o["excerpt"],
            "observed_at": o["observed_at"],
            "role": o["role"] if "role" in o.keys() else None,
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
            "role": (edge["role"] if edge is not None and "role" in edge.keys() else None),
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
    requested = normalize_slug(child_slug)
    with connect() as conn:
        amap = _alias_map(conn)
        norm = _canon_slug(amap, requested)
        child_row = conn.execute(
            "SELECT name FROM strains WHERE slug=?", (norm,)
        ).fetchone()
        child_name = child_row["name"] if child_row else norm
        child_spellings = {norm} | {s for s, c in amap.items() if c == norm}
        placeholders = ",".join("?" * len(child_spellings))
        rows = conn.execute(
            "SELECT parent_slug, source_url, source_title, engine, observed_at "
            f"FROM lineage_sources WHERE child_slug IN ({placeholders}) "
            "AND NOT quarantined",
            tuple(child_spellings),
        ).fetchall()

        # url → assertion (set of parents) + source metadata.
        url_parents: Dict[str, Set[str]] = {}
        url_meta: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            url = r["source_url"]
            url_parents.setdefault(url, set()).add(_canon_slug(amap, r["parent_slug"]))
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

    A human decision supersedes the mechanical unresolved-parent gate:
    quarantining clears ``quarantine_reason`` (the row becomes a plain human
    quarantine the gate passes will never touch), and restoring stamps
    ``HUMAN_APPROVED`` so a later merge never re-gates the approved row.
    Restoring an observation whose parent has no strains row materializes
    that parent — approving the observation asserts the parent.
    """
    norm_child = normalize_slug(child_slug)
    norm_parent = normalize_slug(parent_slug)
    flag = 1 if quarantined else 0
    reason = None if quarantined else HUMAN_APPROVED
    with connect() as conn:
        cur = conn.execute(
            "UPDATE lineage_sources SET quarantined=?, quarantine_reason=? "
            "WHERE child_slug=? AND parent_slug=? AND source_url=?",
            (flag, reason, norm_child, norm_parent, source_url),
        )
        if cur.rowcount == 0:
            raise KeyError(
                f"no observation row for ({norm_child}, {norm_parent}, {source_url})"
            )
        # Matching LINEAGE claims from the same URL follow the observation.
        conn.execute(
            "UPDATE claims SET quarantined=?, quarantine_reason=? "
            "WHERE child_slug=? AND type='LINEAGE' AND source_url=?",
            (flag, reason, norm_child, source_url),
        )
        if not quarantined:
            parent_row = conn.execute(
                "SELECT slug FROM strains WHERE slug=?", (norm_parent,)
            ).fetchone()
            if parent_row is None and lookup_canonical_slug(conn, norm_parent) is None:
                conn.execute(
                    "INSERT INTO strains (slug, name, origin, first_seen, "
                    "last_researched) VALUES (?, ?, 'resolved', ?, ?) "
                    "ON CONFLICT(slug) DO NOTHING",
                    (norm_parent, _slug_display_name(norm_parent), _now(), _now()),
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
# Unresolved-parent gate — merge-time state for claims about parents the KB
# has never seen (see module docstring). The merge pipeline flags; only the
# parent's own research or a curator clears.
# ---------------------------------------------------------------------------

def _sync_claim_gate(conn: sqlite3.Connection) -> None:
    """Keep LINEAGE claim rows in step with their observations' gate state.

    An assertion is a parent-SET, so a claim whose (child, source_url)
    contains any gated observation is held back entirely; a claim whose URL
    no longer hides anything comes back. Only machine-gate reasoning is
    touched here — plain human quarantines (reason NULL) keep hiding.
    """
    conn.execute(
        "UPDATE claims SET quarantined=1, quarantine_reason=? "
        "WHERE type='LINEAGE' AND quarantined=0 AND EXISTS ("
        "  SELECT 1 FROM lineage_sources ls"
        "  WHERE ls.child_slug=claims.child_slug"
        "    AND ls.source_url=claims.source_url"
        "    AND ls.quarantined=1 AND ls.quarantine_reason=?)",
        (UNRESOLVED_PARENT, UNRESOLVED_PARENT),
    )
    conn.execute(
        "UPDATE claims SET quarantined=0, quarantine_reason=NULL "
        "WHERE quarantined=1 AND quarantine_reason=? AND NOT EXISTS ("
        "  SELECT 1 FROM lineage_sources ls"
        "  WHERE ls.child_slug=claims.child_slug"
        "    AND ls.source_url=claims.source_url AND ls.quarantined=1)",
        (UNRESOLVED_PARENT,),
    )


def _run_parent_gate(
    conn: sqlite3.Connection,
    fresh_unknown: Set[Tuple[str, str, str]],
) -> None:
    """Gate fresh observations naming unknown parents, then release any
    gated observation whose parent has since materialized (its own research
    made the name real — the deterministic 'hard verifier'). Runs inside the
    merge transaction, before recompute()."""
    for child_slug, parent_slug, url in fresh_unknown:
        conn.execute(
            "UPDATE lineage_sources SET quarantined=1, quarantine_reason=? "
            "WHERE child_slug=? AND parent_slug=? AND source_url=? "
            "AND quarantined=0 "
            "AND (quarantine_reason IS NULL OR quarantine_reason <> ?)",
            (UNRESOLVED_PARENT, child_slug, parent_slug, url, HUMAN_APPROVED),
        )
    known = set(_alias_map(conn))
    if known:
        conn.execute(
            "UPDATE lineage_sources SET quarantined=0, quarantine_reason=NULL "
            f"WHERE quarantined=1 AND quarantine_reason=? "
            f"AND parent_slug IN ({','.join('?' * len(known))})",
            (UNRESOLVED_PARENT, *known),
        )
    _sync_claim_gate(conn)


def _owner_of_alias(conn: sqlite3.Connection, alias: str) -> Optional[str]:
    """Canonical slug that already claims ``alias``, if any."""
    return _alias_map(conn).get(normalize_slug(alias))


def _attach_alias(conn: sqlite3.Connection, canon_slug: str, alias: str) -> None:
    """Add ``alias`` to ``canon_slug``'s aliases_json. Raises ValueError on
    conflict with another strain's slug or alias."""
    alias_slug = normalize_slug(alias)
    if not alias_slug:
        raise ValueError("alias is required")
    if alias_slug == canon_slug:
        return
    other = conn.execute(
        "SELECT slug FROM strains WHERE slug=?", (alias_slug,)
    ).fetchone()
    if other is not None:
        raise ValueError(
            f"{alias_slug!r} is already a strain slug; aliasing would hide it"
        )
    owner = _owner_of_alias(conn, alias_slug)
    if owner and owner != canon_slug:
        raise ValueError(
            f"{alias_slug!r} is already an alias of {owner!r}"
        )
    row = conn.execute(
        "SELECT aliases_json FROM strains WHERE slug=?", (canon_slug,)
    ).fetchone()
    if row is None:
        raise KeyError(canon_slug)
    current = _parse_aliases_json(row["aliases_json"])
    if alias_slug in current:
        return
    current.append(alias_slug)
    conn.execute(
        "UPDATE strains SET aliases_json=? WHERE slug=?",
        (_dump_aliases(current), canon_slug),
    )


def set_strain_aliases(
    slug: str,
    add: Optional[Iterable[str]] = None,
    remove: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Curator add/remove of aliases on an existing strain.

    Adding an alias that currently sits in the unresolved-parent queue
    releases those observations (the name now points at a real strain).
    Raw ``parent_slug`` values are not rewritten. Raises KeyError for an
    unknown strain and ValueError for a conflicting alias.
    """
    norm = normalize_slug(slug)
    if not norm:
        raise ValueError("slug is required")
    with connect() as conn:
        canon = lookup_canonical_slug(conn, norm) or norm
        row = conn.execute("SELECT * FROM strains WHERE slug=?", (canon,)).fetchone()
        if row is None:
            raise KeyError(norm)
        norm = canon
        for alias in add or []:
            _attach_alias(conn, norm, alias)
        if remove:
            current = _parse_aliases_json(row["aliases_json"])
            drop = {normalize_slug(a) for a in remove if normalize_slug(a)}
            kept = [a for a in current if a not in drop]
            conn.execute(
                "UPDATE strains SET aliases_json=? WHERE slug=?",
                (_dump_aliases(kept) if kept else None, norm),
            )
        _run_parent_gate(conn, set())
        recompute(conn)
        updated = conn.execute(
            "SELECT * FROM strains WHERE slug=?", (norm,)
        ).fetchone()
    return _strain_node(updated)


def resolve_parent(
    parent_slug: str, name: str = "", alias_of: str = ""
) -> Dict[str, Any]:
    """Human approval of a gated parent — the curation path out of the gate.

    Default: creates the strains row when the name has never materialized
    (origin ``'resolved'``), releases every UNRESOLVED_PARENT observation
    citing this parent, and re-derives.

    ``alias_of``: treat the pending name as an alias of an existing strain
    instead of materializing a sibling node. Raw observation slugs stay
    as recorded; ``recompute()`` joins through the alias map.

    Plain human quarantines (reason NULL) are untouched: approving that
    the name is real is not vouching for quarantined evidence. Raises
    KeyError when nothing is pending (or the alias target is unknown)
    and ValueError when aliasing would hide another strain.
    """
    norm = normalize_slug(parent_slug)
    if not norm:
        raise ValueError("parent_slug is required")
    target = normalize_slug(alias_of) if alias_of else ""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE lineage_sources SET quarantined=0, quarantine_reason=NULL "
            "WHERE parent_slug=? AND quarantined=1 AND quarantine_reason=?",
            (norm, UNRESOLVED_PARENT),
        )
        if cur.rowcount == 0:
            raise KeyError(
                f"no unresolved-parent observations pending for {norm!r}"
            )
        if target:
            owner = conn.execute(
                "SELECT slug FROM strains WHERE slug=?", (target,)
            ).fetchone()
            if owner is None:
                raise KeyError(
                    f"alias target {target!r} is not a KB strain"
                )
            _attach_alias(conn, target, norm)
            display = conn.execute(
                "SELECT name FROM strains WHERE slug=?", (target,)
            ).fetchone()["name"]
            canon = target
        else:
            display = (name or "").strip() or _slug_display_name(norm)
            conn.execute(
                "INSERT INTO strains (slug, name, origin, first_seen, "
                "last_researched) VALUES (?, ?, 'resolved', ?, ?) "
                "ON CONFLICT(slug) DO NOTHING",
                (norm, display, _now(), _now()),
            )
            canon = norm
        _sync_claim_gate(conn)
        recompute(conn)
        row = conn.execute(
            "SELECT * FROM strains WHERE slug=?", (canon,)
        ).fetchone()
        amap = _alias_map(conn)
        parent_spellings = {canon} | {s for s, c in amap.items() if c == canon}
        children = sorted({
            _canon_slug(amap, r["child_slug"])
            for r in conn.execute(
                f"SELECT DISTINCT child_slug FROM lineage_sources "
                f"WHERE parent_slug IN ({','.join('?' * len(parent_spellings))}) "
                "AND NOT quarantined",
                tuple(parent_spellings),
            ).fetchall()
        })
    return {
        "parent": _strain_summary_row(row) if row else None,
        "display_name": display,
        "resolved_observations": cur.rowcount,
        "children": children,
        "alias_of": target or None,
        "aliases": _parse_aliases_json(
            row["aliases_json"] if row is not None and "aliases_json" in row.keys() else None
        ),
    }


def pending_parents(limit: int = 200) -> Dict[str, Any]:
    """The review queue: observations held by the unresolved-parent gate,
    grouped by parent name, newest first."""
    limit = max(1, min(int(limit), 500))
    with connect() as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS c FROM lineage_sources "
            "WHERE quarantined=1 AND quarantine_reason=?",
            (UNRESOLVED_PARENT,),
        ).fetchone()["c"]
        rows = conn.execute(
            "SELECT parent_slug, child_slug, source_url, source_title, "
            "engine, excerpt, confidence, observed_at "
            "FROM lineage_sources WHERE quarantined=1 AND quarantine_reason=? "
            "ORDER BY observed_at DESC, parent_slug ASC, child_slug ASC "
            "LIMIT ?",
            (UNRESOLVED_PARENT, limit),
        ).fetchall()

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    order: List[str] = []
    for r in rows:
        parent = r["parent_slug"]
        if parent not in grouped:
            grouped[parent] = []
            order.append(parent)
        grouped[parent].append({
            "child_slug": r["child_slug"],
            "source_url": r["source_url"],
            "source_title": r["source_title"],
            "engine": r["engine"],
            "excerpt": r["excerpt"],
            "confidence": r["confidence"],
            "observed_at": r["observed_at"],
        })
    return {
        "total_observations": total,
        "total_parents": len(order),
        "limit": limit,
        "parents": [
            {
                "parent_slug": parent,
                "parent_name": _slug_display_name(parent),
                "observation_count": len(grouped[parent]),
                "children": grouped[parent],
            }
            for parent in order
        ],
    }


# ---------------------------------------------------------------------------
# Research run history — the research_runs table is written on every merge;
# these accessors make it readable again.
# ---------------------------------------------------------------------------

def _run_row_dict(r: sqlite3.Row) -> Dict[str, Any]:
    def _int(key: str) -> int:
        try:
            return int(r[key] or 0)
        except (IndexError, KeyError):
            return 0

    return {
        "id": r["id"],
        "query": r["query"],
        "started_at": r["started_at"],
        "completed_at": r["completed_at"],
        "providers_used": json.loads(r["providers_used"] or "[]"),
        "sources_count": r["sources_count"],
        "claims_count": r["claims_count"],
        "error": r["error"],
        # LLM spend for the run — 0 when the row predates the columns or
        # no LLM was configured (absent is absent, honestly).
        "llm_usage": {
            "calls": _int("llm_calls"),
            "model": r["llm_model"] if "llm_model" in r.keys() else None,
            "prompt_tokens": _int("prompt_tokens"),
            "completion_tokens": _int("completion_tokens"),
            "total_tokens": _int("total_tokens"),
        },
    }


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
        "runs": [_run_row_dict(r) for r in rows],
    }


def get_research_run(run_id: str) -> Optional[Dict[str, Any]]:
    """One research run row by id, or None."""
    with connect() as conn:
        r = conn.execute(
            "SELECT * FROM research_runs WHERE id=?", (run_id,)
        ).fetchone()
    if r is None:
        return None
    return _run_row_dict(r)


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
        aliases = _parse_aliases_json(
            r["aliases_json"] if "aliases_json" in r.keys() else None
        )
        score = 0
        if needle == name or needle == slug or needle in aliases:
            score = 3
        elif (
            name.startswith(needle)
            or slug.startswith(needle)
            or any(a.startswith(needle) for a in aliases)
        ):
            score = 2
        elif (
            needle in name
            or needle in slug
            or any(needle in a for a in aliases)
        ):
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
        pending = conn.execute(
            "SELECT COUNT(*) AS c FROM lineage_sources "
            "WHERE quarantined=1 AND quarantine_reason=?",
            (UNRESOLVED_PARENT,),
        ).fetchone()["c"]
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
        "pending_parent_observations": pending,
        "node_types": {"Strain": len(strains), "Person": 0, "Claim": n_claims},
        "trust_distribution": tier_counts,
    }
