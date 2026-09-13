# CLAUDE.md

This file provides guidance to Claw Dev (claude.ai/code) when working with code in this repository.

# CRS-01 — Cannabis Research Sentinel

A graph-first intelligence layer for cannabis breeding knowledge. The system handles legacy oral history data where reliability and accuracy are paramount.

## Development Commands

### Backend (FastAPI + Python)

Backend is managed with `uv` (preferred) or `pip`. The virtual environment is at `backend/.venv/`.

```bash
# Run the backend locally (standalone — persists to the SQLite KB)
uvicorn backend.src.main:app --reload --port 8000

# Or from the backend/ directory
uvicorn src.main:app --reload --port 8000

# Install dependencies and dev tools
uv pip install -e backend/
uv pip install -e "backend/[dev]"

# Run the full backend test suite
pytest backend/tests

# Run a single test file
pytest backend/tests/test_curation_api.py

# Run a single test function
pytest backend/tests/test_curation_api.py::test_quarantine_reduces_edge_and_rederives_tier -v
```

The backend reads `.env` from the repository root (resolved in `backend/src/config.py`). It runs standalone on its SQLite knowledge base — no database env vars required. For `List[str]` fields such as `CORS_ORIGINS`, use JSON array syntax in `.env` (e.g., `["http://localhost:3000"]`). See `.env.example` for all variables and notes.

### Frontend (Next.js 15 + React 19)

```bash
# Install dependencies
cd frontend && npm install

# Run the dev server on http://localhost:3000
npm run dev

# Build for production
npm run build

# Start the production server
npm start

# Run ESLint
npm run lint
```

The frontend expects `NEXT_PUBLIC_API_URL` in `.env` (default: `http://localhost:8000/api/v1`).

### Health & Sanity Checks

```bash
# Backend health
curl http://localhost:8000/health

# API docs
curl http://localhost:8000/docs

# KB statistics
curl http://localhost:8000/api/v1/graph/stats
```

## High-Level Architecture

### Data Model & Trust System

The SQLite knowledge base (`backend/src/graph/kb.py`, file `backend/data/crs01.db`, override with `CRS_KB_PATH`) is the source of truth; the graph payloads are derived from it. Raw evidence rows are append-only; aggregates (lineage edges, trust tiers) are recomputed from them. Core record types are `strains`, `sources`, `lineage_sources` (raw observations), and `claims`. Every claim is attributable to a source URL, and every derived verdict carries a trust tier.

| Tier | Color | Display Rule |
|------|-------|--------------|
| VERIFIED | `#D4A017` | Prominent |
| COMMUNITY_CONSENSUS | `#2DD4BF` | Prominent |
| ANECDOTAL | `#94A3B8` | Badge + caveat |
| CONTRADICTED | `#EF4444` | Hidden by default |

The prevailing principle is: **a missing connection is more honest than a false one.** Confidence floats rank and shade the UI; the derived trust tiers are the trust system, and VERIFIED is reserved for human curation.

### Research Pipeline (the old agent contracts, as pipeline stages)

What the original spec called Hunter → Connector → Verifier is now one deterministic pipeline in `backend/src/ingestion/research/orchestrator.py`, with the same conceptual write boundaries:

- **Discovery** (hunter stage) — provider fan-out (Tavily/Perplexity/DDG/Wikipedia) writes only raw evidence: the JSONL ledger, `data/raw/<run_id>/`, and `sources` rows.
- **Linking** (connector stage) — hybrid LLM+regex extraction writes only strains, lineage observations, and LINEAGE claims, all through `kb.merge_research_run()`.
- **Assessment** (verifier stage) — `kb.recompute()` and `claim_tier()` derive tiers from the evidence; they can never produce VERIFIED (human curation only, via the curation routes).

### Backend API Structure

`backend/src/main.py` mounts routers under `/api/v1`. Important endpoints and their routers:

- `/graph/*` — `backend/src/api/routes/graph.py`: strain catalog, strain search, single strain, and KB stats.
- `/neighborhood/*` — `backend/src/api/routes/neighborhood.py`: k-hop subgraph around a strain.
- `/evidence/*` + `/curation/*` — per-strain evidence/conflict drill-down; human tier review and observation quarantine (the only path to VERIFIED).
- `/research/*` — `backend/src/api/routes/research.py`: multi-provider deep research returning a lineage graph.
- `/archive/*` — `backend/src/api/routes/archive.py`: archive.org availability lookup and the sources read path.

Configuration is centralized in `backend/src/config.py` using Pydantic Settings. It resolves `.env` from the repo root regardless of whether the backend is launched from the root or from `backend/`.

### Frontend Structure

The frontend is a Next.js 15 App Router application in `frontend/src/app/`. It is dark mode only. Design tokens are defined in `frontend/src/styles/globals.css` as CSS variables and mirrored in the Tailwind config. The primary color palette is a deep forest green with muted amber accents.

Key components:

- `GraphCanvas.tsx` — the polar confidence wheel (fit + zoom/pan, hover popovers, collision-relaxed placement). Renders the graph pane; selection state lives in `page.tsx`.
- `NodeDetailCard.tsx` — provenance details for a selected wheel node, docked in the page's context rail.
- `ReportView.tsx` — full-width editorial research dossier (cream paper artifact — the one intentional light surface).
- `SourcesView.tsx` — full-width list of every claim with its source URL.
- `ViewSwitcher.tsx` — Graph / Report / Sources / Dashboard tabs.
- `SearchAutocomplete.tsx` and `SuggestionChips.tsx` — drive the main search bar.
- `ResearchingPanel.tsx` — shows in-progress agent state.
- `api-client.ts` — all HTTP calls to the backend, with snake_case → camelCase normalization for orchestrator results.

## Important Constraints & Gotchas

1. **Route order matters in FastAPI.** In `backend/src/api/routes/graph.py`, `/strains` and `/strains/search` are registered before `/strains/{name}` so that literal paths are matched before the path parameter captures them.

2. **Sessions are short-lived.** Every `kb.py` accessor opens its own short-lived SQLite connection (SQLite open is cheap, WAL journal); nothing holds a database session across requests, and tests point `CRS_KB_PATH` at a tmp file per test.

3. **The ingest ledger is append-only.** Every research run appends its claims to the JSONL ledger on disk (`backend/data/ingest_ledger.jsonl`, configurable via `CRS_INGEST_LEDGER`). Do not delete this file unless you intend to clear the audit trace. Snapshot the KB + ledger + `raw/` with `scripts/snapshot_kb.sh`; restore with `scripts/restore_kb.sh`. `provider_cache.db` is a disposable network skip and is never packed.

4. **Confidence floats are an ordering signal, not a truth mechanism.** They rank and shade the UI. Never gate a write on a confidence threshold — tiers are the trust system.

5. **CORS is exact-match.** `CORS_ORIGINS` must list both `localhost` and `127.0.0.1` variants, plus any non-default port. Pydantic Settings v2 requires JSON array syntax in `.env` for `List[str]` fields, not comma-separated strings.

6. **Do not broaden the pipeline write boundaries.** Discovery writes raw evidence only; merging flows through `kb.merge_research_run()`; tier derivation never assigns VERIFIED. See "Research Pipeline" above.

7. **No light mode.** All UI is dark mode only. Trust tier colors are fixed and must not drift — they live in exactly one place each: `TRUST_TIER_META` in `frontend/src/lib/types.ts` and the `--trust-*` variables in `frontend/src/styles/globals.css`. Components import from there; never hardcode tier hexes.

## Key Reference Files

- `SPEC.md` — full implementation specification: architecture, evidence & trust model, write boundaries, verification checklist.
- `backend/src/graph/kb.py` — the SQLite knowledge base (agreement semantics in the module docstring).
- `backend/src/ingestion/research/orchestrator.py` — the research pipeline and its write boundaries.
- `backend/src/ingestion/ledger.py` — the append-only JSONL ledger path and read helper.
- `backend/src/config.py` — environment configuration and defaults.
- `frontend/src/styles/globals.css` — design tokens.
- `frontend/src/components/GraphCanvas.tsx` — primary graph renderer.
- `frontend/src/lib/api-client.ts` — frontend-backend API surface.
