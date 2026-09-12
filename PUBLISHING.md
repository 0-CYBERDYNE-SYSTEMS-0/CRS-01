# Release & publishing workflow

How this repository is published and kept publishable.

## Repository arrangement

- **This repo** (`0-CYBERDYNE-SYSTEMS-0/crs-01`) is the public home of CRS-01. Its git
  history intentionally starts fresh at a single root commit — the pre-public development
  history is **not** distributed.
- **The archive** (`0-CYBERDYNE-SYSTEMS-0/crs-01-private-history`, private) preserves the
  pre-public development history for reference. It must stay private: that history predates
  the public-repo hygiene rules (it contains machine details and AI-assistant state that
  were removed from the published tree).

## Publishing a change

Normal flow — the published history and the working repo are the same branch:

```bash
git add -A
git commit -m "..."
git push          # origin = the public repo; CI runs on every push and PR
```

## Keeping the tree publishable

The `.gitignore` exclusions are deliberate — do not add exceptions for personal or local
files:

- `.env` — the only place keys belong. Any key that appears in a paste, log, ticket, or
  commit is compromised: **rotate it immediately**.
- `.claude/`, `.claw-dev/` — local AI-assistant state.
- `backend/data/raw/`, `backend/data/snapshots/` — runtime fetch payloads and snapshot
  archives. The seed KB (`backend/data/crs01.db`) and the append-only ingest ledger are
  tracked on purpose.

## If sensitive data ever lands in history

1. Rotate the exposed credential first — deleting the commit does not un-leak it.
2. Rewrite with `git filter-repo` (`--invert-paths` for the offending paths, `--mailmap`
   for identities), force-push, and verify with:

   ```bash
   git grep -lE 'tvly-|pplx-|sk-[A-Za-z0-9]{16}' $(git rev-list --all)
   ```

   Every command must print nothing before you consider it clean.
