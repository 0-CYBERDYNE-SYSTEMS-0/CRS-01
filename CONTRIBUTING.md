# Contributing to CRS-01

Thanks for helping build an honest knowledge graph. This project has a few ground rules
that are architectural, not stylistic — please read them before your first PR.

## Setup

Prerequisites: Python 3.11+, Node 18+.

```bash
git clone https://github.com/0-CYBERDYNE-SYSTEMS-0/crs-01.git
cd crs-01
cp .env.example .env

# Backend
python3 -m venv backend/.venv
backend/.venv/bin/pip install -e 'backend[dev]'

# Frontend
cd frontend && npm install
```

Run everything from one terminal with `./dev.sh` (backend on :8000, frontend on :3000), or
use the individual commands in the [README](README.md#quickstart).

## Development workflow

```bash
pytest backend/tests                            # backend suite — keep it green
cd frontend && npm run lint && npm run build    # lint + typecheck
```

- Point tests at scratch data: `CRS_KB_PATH=/tmp/crs-test.db pytest backend/tests` (the
  suite does this per-test already; never run tests against the real KB).
- Network-dependent tests are marked `live` and require `CRS_LIVE_TESTS=1` — they are
  skipped in normal runs and in CI.
- Before risky KB operations locally, snapshot: `scripts/snapshot_kb.sh [label]`.

## Ground rules (architectural — do not bend)

1. **A missing connection is more honest than a false one.** Nothing is fabricated: data
   exists only because research observed it on the open web or a human curated it.
2. **Pipeline write boundaries are locked.** Discovery writes only raw evidence (ledger,
   `data/raw/`, `sources` rows). Extraction/merge writes only strains, lineage
   observations, and LINEAGE claims — and only through `kb.merge_research_run()`. Tier
   derivation (`kb.recompute()`, `claim_tier()`) derives only and can never produce
   VERIFIED — that tier is reserved for human curation via the curation routes.
3. **The SQLite KB is the source of truth** (`backend/src/graph/kb.py`). Graph payloads are
   derived from it. Use short-lived connections per accessor — never introduce a shared
   long-lived connection.
4. **The ingest ledger (`backend/data/ingest_ledger.jsonl`) is append-only.** It is the
   audit trace. Don't delete or rewrite it.
5. **Confidence floats rank, tiers trust.** Confidence is an ordering signal that shades
   the UI — never gate a write on a confidence threshold.
6. **Dark mode only. Trust-tier colors are frozen:** VERIFIED `#D4A017`,
   COMMUNITY_CONSENSUS `#2DD4BF`, ANECDOTAL `#94A3B8`, CONTRADICTED `#EF4444` (hidden by
   default). Import from `TRUST_TIER_META` in `frontend/src/lib/types.ts` or the
   `--trust-*` tokens in `frontend/src/styles/globals.css` — never hardcode the hexes.
7. **FastAPI route order matters:** in `backend/src/api/routes/graph.py`, literal routes
   (`/strains`, `/strains/search`) must be registered before `/strains/{name}`.
8. **CORS is exact-match and settings are JSON:** `CORS_ORIGINS` uses JSON array syntax in
   `.env` (Pydantic Settings v2) and must list both `localhost` and `127.0.0.1` variants.
9. **Never commit secrets.** Keys live in `.env` (gitignored). If a key ever appears in a
   paste, log, or ticket, treat it as compromised and rotate it.

## Pull request checklist

- [ ] `pytest backend/tests` green
- [ ] `cd frontend && npm run lint && npm run build` green
- [ ] Pipeline write boundaries respected (rule 2)
- [ ] Tier colors / dark-mode tokens untouched or imported from the token sources (rule 6)
- [ ] Ledger treated as append-only (rule 4)
- [ ] No secrets, personal paths, or machine-specific config committed (rule 9)
- [ ] New env vars documented in `.env.example`

## Questions

Open an issue. For security matters, use GitHub's private vulnerability reporting (see
[SECURITY.md](SECURITY.md)).
