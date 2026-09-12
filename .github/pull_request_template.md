## What does this PR do?

<!-- Describe the change and why it's needed. Link related issues. -->

## How was it tested?

- [ ] `pytest backend/tests` green
- [ ] `cd frontend && npm run lint && npm run build` green

## Ground rules (see CONTRIBUTING.md)

- [ ] Pipeline write boundaries respected: discovery → raw evidence only; merges via
      `kb.merge_research_run()`; tier derivation can never produce VERIFIED
- [ ] Trust-tier colors unchanged / imported from token sources, dark mode only
- [ ] Ingest ledger treated as append-only
- [ ] No secrets, personal paths, or machine-specific config committed
- [ ] New env vars documented in `.env.example`
