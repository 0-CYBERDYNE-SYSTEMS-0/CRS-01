# CRS-01 — Cannabis Research Sentinel

A graph-first intelligence layer for cannabis breeding knowledge. CRS-01 researches cannabis
strain lineage across the open web, merges what it finds into an evidence-backed knowledge
graph, and presents every verdict with its provenance — so **a missing connection is always
more honest than a false one.**

> CRS-01 is a research and reference tool for breeding genetics. It is not medical advice,
> and nothing in the knowledge graph is a claim about safety or efficacy.

## What it does

- **Researches** — a deterministic pipeline fans out across search providers (Tavily,
  Perplexity, DuckDuckGo, Wikipedia), optionally checks archive.org for durable captures,
  and writes raw evidence to an append-only JSONL ledger.
- **Extracts & merges** — a hybrid LLM + regex extractor pulls parent-strain lineage
  observations and merges them into the SQLite knowledge base as claims attributable to
  source URLs.
- **Derives trust** — trust tiers are computed from evidence agreement, never asserted by
  the pipeline. Contradictions are kept and marked, not deleted.
- **Curates** — a human curation API is the only path to the VERIFIED tier.

## The trust model

Every claim is attributable to a source URL. Tiers derive from the evidence:

| Tier | Meaning | Color | Display |
|------|---------|-------|---------|
| VERIFIED | Human-curated (never assigned automatically) | `#D4A017` | Prominent |
| COMMUNITY_CONSENSUS | 2+ independent domains agree | `#2DD4BF` | Prominent |
| ANECDOTAL | Single source | `#94A3B8` | Badge + caveat |
| CONTRADICTED | Conflicting parent sets (kept, not deleted) | `#EF4444` | Hidden by default |

Confidence floats only rank and shade the UI — the tiers are the trust system.

## Architecture

- **Backend** — FastAPI (Python 3.11+). The SQLite KB (`backend/src/graph/kb.py`, file
  `backend/data/crs01.db`, override with `CRS_KB_PATH`) is the source of truth; graph
  payloads are derived from it. Routers under `backend/src/api/routes/` mount at `/api/v1`:
  `graph`, `neighborhood`, `evidence`, `curation`, `research`, `archive`.
- **Research pipeline** — `backend/src/ingestion/research/orchestrator.py`, with strict
  write boundaries: discovery writes only raw evidence; extraction/merge writes only
  through `kb.merge_research_run()`; tier derivation (`kb.recompute()`, `claim_tier()`)
  can never produce VERIFIED.
- **Frontend** — Next.js 15 / React 19 App Router app in `frontend/`, dark mode only.
  `api-client.ts` owns all HTTP calls and snake_case → camelCase normalization. Highlights:
  the polar confidence wheel (`GraphCanvas.tsx`), the editorial research dossier
  (`ReportView.tsx`), and the per-claim source listing (`SourcesView.tsx`).

## Quickstart

Prerequisites: Python 3.11+, Node 18+.

```bash
cp .env.example .env    # all keys are optional; see notes inside

./dev.sh                # backend on :8000 + frontend on :3000 from one terminal
```

Or separately:

```bash
python3 -m venv backend/.venv && backend/.venv/bin/pip install -e 'backend[dev]'
uvicorn backend.src.main:app --reload --port 8000

cd frontend && npm install && npm run dev
```

Sanity checks: `curl http://localhost:8000/api/v1/graph/stats`, or the API docs at
<http://localhost:8000/docs>.

`OPENAI_API_KEY` is optional — without it the regex extractor runs alone; with it, lineage
extraction is LLM-assisted. Search-provider keys (Tavily, Perplexity) unlock the
corresponding discovery providers.

## Testing

```bash
pytest backend/tests                            # backend suite (no network by default)
cd frontend && npm run lint && npm run build    # lint + typecheck
```

Tests point `CRS_KB_PATH` at scratch databases, so nothing touches `backend/data/crs01.db`.
Tests marked `live` additionally require `CRS_LIVE_TESTS=1` and network access.

## Repo layout

```
backend/
  src/graph/kb.py                  SQLite knowledge base (source of truth)
  src/ingestion/research/          the research pipeline + write boundaries
  src/ingestion/ledger.py          append-only JSONL evidence ledger
  src/api/routes/                  FastAPI routers (/api/v1)
  data/                            crs01.db + ingest ledger (tracked seed data)
frontend/
  src/app/                         App Router pages
  src/components/                  confidence wheel, dossier, sources, dashboard
  src/lib/api-client.ts            backend HTTP + normalization
scripts/snapshot_kb.sh             snapshot KB + ledger + raw/ before risky operations
scripts/restore_kb.sh <tarball>    restore a snapshot into backend/data/
```

`backend/data/` ships a working seed KB so a fresh clone is immediately useful. The DB and
the ingest ledger are tracked on purpose (the ledger is the audit trace); `data/raw/`,
`data/snapshots/`, and `data/provider_cache.db` (disposable wiki/search TTL cache) are
local runtime artifacts. `scripts/snapshot_kb.sh` packs `crs01.db` + ledger + `raw/`;
it does not pack the provider cache. Restore with `scripts/restore_kb.sh <tarball>`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the architectural ground rules (pipeline
write boundaries, tier semantics, fixed design tokens), and the PR checklist. By
participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). For security issues,
see [SECURITY.md](SECURITY.md) — please use GitHub's private vulnerability reporting rather
than public issues.

## License

[MIT](LICENSE)
