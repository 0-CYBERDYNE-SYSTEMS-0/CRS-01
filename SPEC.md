# CRS-01 — Cannabis Research Sentinel
## Implementation Specification v2.0
### Status: ACTIVE — replaces the 2026-04 v1.0 draft entirely

---

## 0. Why this rewrite exists

The v1.0 draft specced an architecture that was never built: a Neo4j source of
truth, a LangGraph multi-agent swarm, forum crawlers, S3/Postgres storage, a
GraphQL overlay, Qdrant vectors, projects and auth. The system that actually
got built — and shipped to testers — converged on something better and simpler:
a deterministic research pipeline, an append-only SQLite evidence store with
derived trust tiers, and one honest UI. Worse than merely stale, v1.0 had come
to **contradict the product's locked semantics** (it promised auto-VERIFIED
from lab data and 3-source consensus; the locked model is human-only VERIFIED
and 2-domain consensus).

This document describes the system that exists, records the decisions that are
locked, and specs the 2026-09 simplification plan (§7). **Where v1.0 disagreed
with this document, this document wins.** The full first-principles analysis
behind §7 lives in `crs01-first-principles-review.html`.

**Governing principle (unchanged since v1.0, and still the whole point):**
*When in doubt, the system writes nothing. A missing connection is more honest
than a false one.*

---

## 1. What CRS-01 is

A graph-first intelligence layer for cannabis breeding knowledge. Legacy
breeding knowledge is oral history scattered across forums, seed banks, and
Wikipedia — a single wrong lineage claim can invalidate years of breeding work.
CRS-01 accumulates that knowledge under an evidence discipline:

- **Nothing is fabricated.** A row exists only because a research run observed
  it on the open web, or because a human curator added it.
- **Trust is derived, never asserted.** Tiers come from countable properties of
  the evidence (how many independent domains agree; whether parent-sets
  conflict; a human's signature).
- **Everything is auditable.** Every derived verdict drills down to the raw
  observations behind it, with excerpts and archived copies where they exist.

Single-curator research tool. No auth, no multi-tenancy, no billing.

---

## 2. Architecture as built

```
Search bar
  └─ POST /research/submit  (the one real write path)
       ResearchOrchestrator.run(query)
         1. Provider fan-out      Tavily? Perplexity? + DuckDuckGo + Wikipedia
            (graceful degrade to [] per provider; Perplexity auth circuit breaker)
         2. Content gates         lenient for the subject query, strict
                                  cannabis-only for recursed parents
         3. Raw persistence       data/raw/<run_id>/*.json (chain of custody)
         4. Extraction            LLM (source_index-anchored, unanchored rows
                                  dropped) + regex patterns as complement/fallback
         5. Name canonicalization aliases folded, junk prose names dropped
         6. Tier stamping         backend is tier authority (see §3)
         7. Recursion             into discovered parents (bounded depth/nodes)
         8. Ledger append         data/ingest_ledger.jsonl (append-only audit trace)
       kb.merge_research_run(run)
         sources → strains → lineage_sources (append-only) → claims
         → recompute(): lineage_edges + strain tiers derived from raw rows
       (opt-in) Wayback autolookup  CRS_WAYBACK_AUTOLOOKUP, default OFF,
                                   fail-open, records only observed captures
  └─ GET /graph/strains/{slug}/neighborhood  (TTL-cached, ETag)
       → GraphCanvas polar wheel + ReportView dossier + SourcesView
  └─ Unknown strain → 404 → frontend auto-runs research → graph appears
```

**Source of truth: the SQLite KB** (`backend/data/crs01.db`, override
`CRS_KB_PATH`). The graph is *derived* from it. There is no second store.
(§7 removes the dead Neo4j layer that pretended otherwise.)

### Module map (post-§7)

| Module | Role | Status |
|---|---|---|
| `backend/src/graph/kb.py` | Evidence store: schema, migrations, writers, `recompute()`, readers, curation | **keep — heart of system** |
| `backend/src/ingestion/research/` | `orchestrator.py` (pipeline), `providers.py`, `extractor.py`, `llm_extractor.py`, `names.py`, `wikipedia_full.py` | keep |
| `backend/src/ingestion/archive.py` | Wayback availability, fail-open; opt-in autolookup | keep |
| `backend/src/api/routes/` | graph, neighborhood, evidence, curation, research, archive | keep |
| `backend/src/api/routes/ingest.py`, `ingestion/search_provider.py`, `normalizer.py` | legacy second search track | **delete (§7 W2)** |
| `backend/src/agents/`, `graph/{client,schema,queries,events}.py`, `routes/agents.py` | spec-era mock/Neo4j layer, no product callers | **delete (§7 W1)** |
| `frontend/src/components/` | GraphCanvas (wheel), ReportView (dossier), SourcesView, DashboardView, NodeDetailCard, ConflictsPanel, MethodologyDrawer, ResearchingPanel, SearchAutocomplete | keep (dedup per §7 W3) |
| `frontend/src/lib/` | `api-client.ts` (all HTTP + snake→camel), `types.ts`, `time.ts` | keep (dead mass out per §7 W3) |

### Commands

```bash
./dev.sh                                        # backend :8000 + frontend :3000
pytest backend/tests                            # full suite (from backend/ for src imports)
pytest backend/tests/test_curation_api.py -v    # single file
cd frontend && npm run lint && npm run build    # lint + typecheck via build
curl http://localhost:8000/api/v1/graph/stats   # sanity check (post-§7)
scripts/snapshot_kb.sh [label]                  # snapshot KB + ledger + raw/ (not provider cache)
scripts/restore_kb.sh <tarball>                 # restore a snapshot into backend/data/
```

Backend reads `.env` from the repo root (resolved in `backend/src/config.py`).
Point tests at a scratch DB with `CRS_KB_PATH=<tmpfile>`; conftest does this
automatically per test.

---

## 3. Evidence & trust model (LOCKED 2026-08-19 — supersedes v1.0 §3)

### 3.1 Storage discipline

- **Raw evidence is append-only.** `lineage_sources` keeps one row per
  `(child, parent, source_url)` observation. `sources` and the JSONL ledger
  likewise. Nothing raw is ever updated destructively or deleted.
- **Aggregates are derived.** `lineage_edges` and strain tiers are rebuilt by
  `recompute()` after every merge — the data always speaks from the evidence.
- **Curation quarantines, never deletes.** A bad observation is flagged out of
  every aggregate; the row stays on file.
- **An assertion is a parent-SET.** One source URL asserting a 3-way cross is
  ONE assertion of three parents, not three pairwise assertions.
- **Unresolved parents are gated, not materialized.** An observation naming a
  parent strain the KB has never seen is recorded but stamped
  `quarantine_reason='UNRESOLVED_PARENT'`: it enters no aggregate and no
  strain node is created for the name. The gate clears when the parent
  becomes a real KB strain through its own research (a deterministic pass in
  `merge_research_run`), or a curator resolves it
  (`POST /graph/parents/{slug}/resolve`). Human quarantine/restore decisions
  (`HUMAN_APPROVED`) always supersede the mechanical gate — a human-restored
  observation is never re-gated by a later merge.

### 3.2 Tier semantics

| Tier | Color | Meaning | Assigned by |
|---|---|---|---|
| `VERIFIED` | `#D4A017` | A human curator vouches for it | **Humans only** — `POST /graph/strains/{slug}/review`. Never assigned automatically, by any agent, LLM, or threshold. Ever. |
| `COMMUNITY_CONSENSUS` | `#2DD4BF` | 2+ independent **domains** agree on the tuple | `recompute()` derivation |
| `ANECDOTAL` | `#94A3B8` | A single source asserts it (also the honest floor when all observations are quarantined) | derivation / default |
| `CONTRADICTED` | `#EF4444` | Two sources assert parent-sets for the same child that are neither equal nor subsets | derivation; **kept, hidden by default**; not human-assignable |

Rules v1.0 had that are **deliberately dropped**: 3-source thresholds,
time-based promotion ("90 days without contradiction"), auto-VERIFIED from lab
data, confidence-band tier mapping. Domains — not raw source counts — are the
consensus unit, because ten scraped mirrors of one seed-bank page are one voice.

### 3.3 Confidence

A 0–1 float ordering signal (extraction-pattern specificity, snippet richness,
model self-report). It ranks and shades the UI. It is **not** a truth claim and
never gates a write. Do not build more machinery on top of it.

### 3.4 The human loop

- `POST /graph/strains/{slug}/review` — the only path to `VERIFIED` (or to
  clearing a verdict back to derivation). Stamps `curated_*` provenance.
- `POST /graph/observations/quarantine` — flag/restore one raw observation;
  everything re-derives. Restoring stamps `HUMAN_APPROVED` (exempt from the
  unresolved-parent gate forever) and materializes the parent node if the
  observation's parent had none.
- `GET /graph/parents/pending` — the unresolved-parent gate review queue.
- `POST /graph/parents/{slug}/resolve` — approve a gated parent name:
  materialize the node, release its gated observations.
- Curated tiers outrank machine verdicts across recomputes; clearing a
  curation returns the strain to evidence-derived truth.

---

## 4. Pipeline write boundaries (replaces v1.0 §4 agent contracts)

v1.0 specced Hunter/Connector/Verifier as bounded agent classes with tool
matrices. The shipped system implements the same *conceptual* boundaries as
stages of one deterministic pipeline, and keeps the vocabulary honestly
(`run.stages` labels: hunter / connector / verifier):

| v1.0 concept | Where it lives now | Boundary |
|---|---|---|
| Hunter (discover + archive) | Provider fan-out + raw persistence | Writes only raw evidence (ledger, `raw/`, `sources`) |
| Connector (link strains) | Extraction → `merge_research_run` | Writes only strains, lineage observations, LINEAGE claims |
| Verifier (assess trust) | `recompute()` + `claim_tier()` | Derives tiers only; **cannot produce VERIFIED** |

These boundaries are architectural. Do not broaden them. The two invariants to
hold in any future change: (1) all pipeline writes flow through
`merge_research_run()`; (2) only the curation routes touch `curated_*`.

---

## 5. API surface (post-§7)

```
GET  /health                                          liveness + KB counts
GET  /graph/strains                                   catalog (list)
GET  /graph/strains/search?q=                         autocomplete
GET  /graph/strains/{name}                            single strain (+ fuzzy 404 suggestions)
GET  /graph/strains/{slug}/neighborhood?depth=0..3    k-hop subgraph (ETag, TTL cache)
GET  /graph/strains/{slug}/evidence                   raw observations behind every edge
GET  /graph/strains/{slug}/conflicts                  derived conflicting parent-set assertions
POST /graph/strains/{slug}/review                     human tier verdict (only path to VERIFIED)
POST /graph/observations/quarantine                   flag/restore a raw observation
GET  /graph/parents/pending                           unresolved-parent gate review queue
POST /graph/parents/{slug}/resolve                    approve a gated parent name
GET  /graph/stats                                     KB statistics
POST /research/submit                                 deep research → ledger → KB merge → neighborhood
GET  /research/by-strain?slug=                        ledger claims previously persisted for a strain
GET  /research/runs                                   run history
GET  /research/runs/{id}                              one run row
POST /archive/lookup                                  archive.org availability (fail-open, persists hits)
GET  /archive/sources                                 sources table read
```

Route-order gotcha: in `graph.py`, literal `/strains` and `/strains/search`
must register before `/strains/{name}`. Honesty endpoints that answer 501 with
an explanation (`/archive/wayback`) stay — they are documentation.

---

## 6. Frontend contract

- Next.js 15 / React 19, App Router, **dark mode only**, earth palette. Design
  tokens in `frontend/src/styles/globals.css`.
- **Tier colors are fixed and must not drift** (§3.2 table). One source of
  truth: `TRUST_TIER_META` / `getTrustTierColor` / `--trust-*` CSS vars.
  Components must not hardcode tier hexes (§7 W3 removes the seven existing
  copies; `MethodologyDrawer` is the reference pattern).
- `api-client.ts` owns all HTTP + snake→camel normalization; no component
  fetches directly.
- Views: Graph (polar wheel) / Report (dossier) / Sources / Dashboard, switched
  by `ViewSwitcher`, deep-linkable via `?strain=` / `?view=`.
- Unknown strain → auto-research → graph appears; `ResearchingPanel` narrates
  the run with per-stage honesty.
- The browser talks only to the frontend origin; `next.config.ts` rewrites
  proxy `/api/v1/*` to FastAPI.

---

## 7. Simplification plan (2026-09 review — the changes to make)

Preference order: **delete > simplify > optimize > automate.** No automation is
proposed. Each wave ships green independently. Line counts are exact.

### Wave 1 — Delete the ghost backend layer (zero behavior change)

| Delete | Lines | Verified safe because |
|---|---|---|
| `backend/src/agents/` (6 files) | 1,115 | Mock data (`MOCK_SOURCES`/`MOCK_CONNECTIONS`/`MOCK_CLAIMS`) behind live endpoints; only caller is the agent route |
| `backend/src/api/routes/agents.py` | 115 | Its client fns have 0 frontend callers; in-memory run store; references nonexistent "projects" |
| `backend/src/graph/schema.py` | 309 | Only consumer `init_schema()` is never called |
| `backend/src/graph/queries.py` | 389 | 0 importers |
| `backend/src/graph/events.py` | 363 | Only importers are the mock agents |
| `backend/src/graph/client.py` | 57 | Only the lifespan ping remains after W1's main.py edit |
| `tests/test_agents.py`, `tests/test_graph_schema.py` | 233 | Test mocks/Cypher strings; schema test asserts rejected v1.0 semantics |
| Dead models in `api/schemas.py` (keep `HealthResponse` only) | ~85 | Routes define their own models |
| `main.py` lifespan ping; `config.py` Neo4j+S3+agent knobs; matching `.env.example` blocks; `graph_connected` health field | ~30 | The field reports a connection nothing uses |
| pyproject deps: `neo4j`, `langgraph`, `langgraph-sdk`, `structlog`, `pytest-asyncio`, `pytest-mock` | 6 deps | 0 imports in `src/`+`tests/` |
| `infra/` (compose + 2 Dockerfiles) | 3 files | Never built successfully (Dockerfile order bugs, nonexistent `standalone` output, dead mount, unreachable env) |

Also: `git rm -r preview/` (17 tracked files — design gallery, design is
locked), `.task.md`, `TODOS.md` (both stale/misleading), root
`package-lock.json` (empty), `scripts/install.sh` (writes env the backend never
reads + an invalid provider value), `scripts/cleanup_kb.py` (one-shot migration
already executed), empty `backend/src/storage/` + `backend/src/api/schemas/`
dirs, `.venv-boot/`. Update `AGENTS.md`/`CLAUDE.md`: "SQLite KB is the source of
truth; the graph is derived"; drop compose/test-agents references; sanity-check
curl → `/graph/stats`. Strip `snapshot_kb.sh`'s two dead branches (`kb/` dir,
`seed/strains.json`).

### Wave 2 — Collapse to one search stack (one UI rewiring)

| Delete | Lines | Replaced by |
|---|---|---|
| `ingestion/search_provider.py` | 257 | Research providers already do this (incl. a duplicate Wikipedia client) |
| `ingestion/normalizer.py` | 59 | Length-derived confidence shouldn't be shown at all |
| `api/routes/ingest.py` | 162 | Deepen → `POST /research/submit`; 404-restore → existing `GET /research/by-strain` + `fetchResearchByStrain` |
| `tests/test_ingest.py` + live half of `test_wikipedia_provider.py` | ~230 | Track-A tests; live coverage duplicates `test_research.py` |

This closes the two-writer race on `researchClaims` (both paths now speak the
research claim shape) and removes the second record format from the ledger.

### Wave 3 — Frontend dead mass + one palette (zero behavior change)

| Delete | Size |
|---|---|
| 12 uncalled `api-client.ts` functions + `ArchiveLookupResult` (grep-verified 0 callers) | ~215 lines |
| ~14 unused `types.ts` types (React Flow, Project, agent results, node schemas, helpers) | ~160 lines |
| npm deps: `@xyflow/react`, `@radix-ui/*` ×4, `class-variance-authority`, `clsx`, `tailwind-merge`, `lucide-react`, `framer-motion`, `@tailwindcss/typography`; move `typescript` to devDeps | 11 deps |
| `next/head` import+usage (no-op in App Router); `serverActions.allowedOrigins` (no server actions) | ~15 lines |
| Dead branches: `SuggestionChips` `stack` variant, `EmptyState` `variant`/always-false `researching`, `ViewSwitcher` `disabled` | ~80 lines |
| Orphan endpoints: `/graph/snapshot` + `kb.snapshot()` + `GraphSnapshotResponse` (worst function in the KB: ≤100 × full-neighborhood BFS, no callers); `/graph/persons/{handle}` + `kb.get_person()` (fabricates a synthetic node) | ~70 lines |
| globals.css Google-Fonts `@import` (double-loads next to `next/font`), unused animations/utilities/tokens; `tailwind.config` dead extends + `src/pages` glob | ~60 lines |

Then simplify (S-items):
- **S1 (doctrine-critical):** route all tier colors through
  `getTrustTierColor`/CSS vars; delete `TIER_COLOR` (DashboardView), the four
  `tierColor()` wrappers, the seven hardcoded palette blocks, the
  SearchAutocomplete ternary.
- **S2:** delete local time-helper copies in `ConflictsPanel`/`ResearchingPanel`
  (the ResearchingPanel copy drops the epoch `×1000` and renders absurd ages —
  **bug B1**); one shared `slugify` from `kb.normalize_slug` (inline
  `.replace(" ","-")` copies in the orchestrator/tables disagree on irregular
  whitespace and silently drop strain metadata — **bug B2**); one `IngestClaim`.
- **S5:** `page.tsx` — replace the `handleRetry` clear-and-timeout hack with the
  existing `kbVersion` nonce; drop `autoResearching`; delete the
  `reportBadge`/`claimCount` duplicate; extract the 220-line `SubjectRail`;
  `ConflictsPanel` mount-effect calls `load()`.

### Wave 4 — Small honesty fixes

- **S3:** one ledger-read helper next to one ledger-path constant (currently
  defined twice, scanned inline twice).
- **S4:** collapse kb.py's four `_ensure_*_columns` helpers into one; delete the
  dead `child_sets` block inside `recompute()` (~lines 443–447); fix
  `stats().total_nodes` (`len(strains) + 0` — fossil `+ 0`, and the name
  excludes Claim nodes that are graph nodes).
- **S6:** register/enable real gating for live-network tests
  (`CRS_LIVE_TESTS=1`); keep exactly one live-gated set (**bug B4**: the
  `pytest.mark.slow` marker and the documented `RUN_LIVE=1` gate both gate
  nothing); delete the conftest-self-check tests in `test_strain_catalog.py`
  (91–103); drop `test_ingest.py`'s shadowing `client` fixture.
- `.gitignore` `videos/`; remove `.DS_Store` files.

**Explicitly kept:** `backend/data/` snapshots in git (audit-trace doctrine),
`dev.sh`, the locked v15 wheel+dossier fusion, all §3 semantics, the test
conftest design, the 501 honesty endpoints.

### What NOT to build (rejections recorded)

No Neo4j reintroduction without a query that SQLite demonstrably can't serve.
No LLM agent loops — the deterministic pipeline + derived tiers is the product.
No Qdrant/embeddings, no GraphQL overlay, no S3, no projects/auth, no second
ingest path, no tier promotion rules involving time.

---

## 8. Verification checklist (updated for the real system)

1. **Round trip** — `POST /research/submit {query}` returns a run; the strain
   then resolves via `GET /graph/strains/{slug}/neighborhood`.
2. **Ledger discipline** — every submitted run appends to
   `data/ingest_ledger.jsonl`; nothing ever rewrites or truncates it.
3. **Tier derivation** — a tuple observed from 2 domains flips to
   `COMMUNITY_CONSENSUS` after merge; a single-domain tuple stays `ANECDOTAL`.
4. **Conflict handling** — two incomparable parent-sets yield `CONTRADICTED`,
   visible via `/conflicts`, hidden by default in the wheel.
5. **VERIFIED is human-only** — no pipeline path produces it; review is the
   only grant, with `curated_*` provenance stamped.
6. **Quarantine** — quarantining an observation re-derives tiers; the row
   survives on disk.
7. **Honest absence** — unknown strain 404s with fuzzy suggestions, never a
   fabricated node; `wayback_url` NULL means "no capture on record."
8. **Tests green offline** — `pytest backend/tests` passes with no network
   (live tests skipped unless opted in).
9. **No tier-color drift** — `grep -r "#2DD4BF\|#D4A017\|#94A3B8\|#EF4444"`
   in `frontend/src/components/` returns only token definitions.
10. **Unresolved-parent gate** — a merge whose claim names an unknown parent
    creates no node and no edge; the observation lands in
    `/graph/parents/pending` and stays out of every aggregate until the
    parent is researched into existence or a curator resolves it.

---

## 9. Decision log

| Date | Decision |
|---|---|
| 2026-04 | v1.0 spec drafted (agent swarm, Neo4j, phases). Superseded. |
| 2026-08-19 | Evidence model locked: append-only raw rows, derived aggregates, 2-domain consensus, human-only VERIFIED, quarantine-not-delete. Mock dataset removed ("no mock nothing"). |
| 2026-09-03 | Immersive 70/30 graph workspace shipped (wheel zoom/pan, full-width dossier). |
| 2026-09-08 | First-principles review (this document): SPEC rewritten to match reality; §7 deletion/simplification plan approved for execution in four waves. |

---

*This spec is the contract. Edits are made here first, not in Slack or DMs.
The companion analysis for §7 is `crs01-first-principles-review.html`.*
