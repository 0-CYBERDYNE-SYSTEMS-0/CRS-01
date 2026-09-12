# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this is

CRS-01 ("Cannabis Research Sentinel") — a graph-first intelligence layer for cannabis breeding knowledge. FastAPI backend (`backend/`) + Next.js 15 / React 19 frontend (`frontend/`). Nothing is fabricated: data exists only because research observed it on the open web or a human curated it. **A missing connection is more honest than a false one.**

## Commands

```bash
./dev.sh                          # run backend (:8000) + frontend (:3000) from one terminal
uvicorn backend.src.main:app --reload --port 8000   # backend only (venv at backend/.venv)
pytest backend/tests              # full backend suite
pytest backend/tests/test_curation_api.py -v   # single test
cd frontend && npm install && npm run dev           # frontend only
npm run lint && npm run build                       # from frontend/ — lint & typecheck via build
curl http://localhost:8000/api/v1/graph/stats       # sanity check the graph API
```

Backend reads `.env` from the repo root (resolved in `backend/src/config.py`), never from `backend/`. See `.env.example`. Point tests at a scratch DB with `CRS_KB_PATH=<tmpfile>`.

## Architecture

- **SQLite KB (backend/src/graph/kb.py, file backend/data/crs01.db, override CRS_KB_PATH) is the source of truth; the graph payloads are derived from it.**
- **KB evidence model (locked with product owner 2026-08-19):** raw evidence rows are append-only; aggregates like `lineage_edges` are derived and recomputed after each merge. Tier semantics: green = 2+ independent domains agree → COMMUNITY_CONSENSUS; amber = single source → ANECDOTAL; red = conflicting parent sets → CONTRADICTED (kept, not deleted). **VERIFIED is reserved for human curation and is never assigned automatically.**
- **Pipeline write boundaries (the old Hunter/Connector/Verifier contracts, now stages of one deterministic pipeline in `backend/src/ingestion/research/orchestrator.py`):** discovery writes only raw evidence (ledger, `raw/`, `sources`); extraction/merge writes only strains, lineage observations, and LINEAGE claims — all through `kb.merge_research_run()`; tier derivation (`recompute()`, `claim_tier()`) derives only, and can never produce VERIFIED. **Do not broaden these — they are architectural.**
- **API:** routers mounted under `/api/v1` in `backend/src/main.py`: graph, neighborhood, evidence (per-strain evidence/conflict drill-down), curation (human tier review + observation quarantine — the only path to VERIFIED), research, archive.
- **Frontend:** App Router app in `frontend/src/app/`, components in `frontend/src/components/`. `api-client.ts` owns all HTTP calls and snake_case→camelCase normalization. Showcase views (Graph/Dashboard/etc.) switch via `ViewSwitcher.tsx`.

## Gotchas

1. **FastAPI route order matters**: in `backend/src/api/routes/graph.py`, literal routes `/strains` and `/strains/search` must be registered before `/strains/{name}`.
2. **The ingest ledger (`backend/data/ingest_ledger.jsonl`) is append-only** — it is the audit trace. Never delete it casually. Snapshot everything under `backend/data/` with `scripts/snapshot_kb.sh [label]`.
3. **Short-lived SQLite connections everywhere.** Every `kb.py` accessor opens its own short-lived connection (SQLite open is cheap, WAL journal) — this sidesteps cross-thread issues under FastAPI. Do not introduce a shared long-lived connection.
4. **Confidence floats are an ordering signal, not a truth mechanism.** They rank and shade the UI; tiers (§ Architecture) are the trust system. Never gate a write on a confidence threshold.
5. **CORS is exact-match** and Pydantic Settings v2 requires JSON array syntax for list env vars: `CORS_ORIGINS=["http://localhost:3000"]` — not comma-separated. List both `localhost` and `127.0.0.1`.
6. **Dark mode only.** No light theme. Trust-tier colors are fixed and must not drift: VERIFIED `#D4A017`, COMMUNITY_CONSENSUS `#2DD4BF`, ANECDOTAL `#94A3B8` (badge + caveat), CONTRADICTED `#EF4444` (hidden by default). Design tokens live in `frontend/src/styles/globals.css`.

## Read before touching sensitive areas

- `SPEC.md` — full spec: architecture, evidence & trust model, write boundaries, verification checklist.
- `backend/src/graph/kb.py` — SQLite KB design notes (module docstring documents the agreement semantics).
- `backend/src/ingestion/research/orchestrator.py` — the research pipeline and its write boundaries.
- `CLAUDE.md` — longer-form developer guide covering the same architecture in more depth.
