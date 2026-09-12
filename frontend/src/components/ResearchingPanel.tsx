"use client";

import type { LineageClaim } from "@/lib/types";
import { getTrustTierLabel, getTrustTierColor } from "@/lib/types";
import type { StageInfo } from "@/lib/types";

interface ResearchingPanelProps {
  strain: string;
  claims: LineageClaim[];
  loading: boolean;
  error: string | null;
  provider?: string;
  fromLedger?: boolean;
  stages?: StageInfo[];
  onDismiss: () => void;
  onRetry: () => void;
}

/** The assertion a lineage claim makes, in the dossier's display form. */
function claimValue(claim: LineageClaim, strain: string): string {
  const extra = claim.extra_parents?.length
    ? ` × ${claim.extra_parents.join(" × ")}`
    : "";
  return `${claim.child || strain} = ${claim.parent_a} × ${claim.parent_b}${extra}`;
}

export default function ResearchingPanel({
  strain,
  claims,
  loading,
  error,
  provider,
  fromLedger,
  stages,
  onDismiss,
  onRetry,
}: ResearchingPanelProps) {
  return (
    <div className="flex h-full w-full flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-baseline justify-between border-b border-[var(--border)] px-6 py-4">
        <div>
          <div className="text-[10px] uppercase tracking-[0.16em] text-[var(--text-faint)]">
            {fromLedger ? "From ledger" : "Live research"}
          </div>
          <div className="font-serif text-[22px] italic text-[var(--text-primary)]">
            {strain}
          </div>
          <div className="mt-1 text-[11px] text-[var(--text-muted)]">
            {loading
              ? "Searching the open web…"
              : error
              ? "Search failed"
              : `${claims.length} claim${claims.length === 1 ? "" : "s"} on file${
                  fromLedger
                    ? " · pulled from JSONL ledger"
                    : provider
                    ? ` · via ${provider}`
                    : ""
                }`}
          </div>
        </div>
        <div className="flex items-center gap-2">
          {fromLedger && !loading && (
            <button
              type="button"
              onClick={onRetry}
              className="rounded-md border border-[var(--border)] bg-transparent px-2.5 py-1 text-[10px] uppercase tracking-[0.08em] text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
              title="Search again to refresh"
            >
              Research again
            </button>
          )}
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Dismiss research panel"
            className="rounded-md border border-transparent px-2 py-1 text-[18px] text-[var(--text-muted)] hover:border-[var(--border)] hover:text-[var(--text-primary)]"
          >
            ×
          </button>
        </div>
      </div>

      {/* ── Agent stages timeline ────────────────────────────── */}
      {stages && stages.length > 0 && (
        <div className="border-b border-[var(--border)] px-6 py-3">
          <div className="flex gap-3" role="list">
            {stages.map((s) => (
              <div
                key={s.agent}
                className="flex-1 rounded-lg border border-[var(--border)] bg-[var(--bg-surface)]/60 p-2.5 text-center"
              >
                <div className="mb-1 text-[10px] uppercase tracking-[0.1em] text-[var(--accent)]">
                  {s.label}
                </div>
                <div className="text-[12px] font-medium text-[var(--text-primary)]">
                  {s.summary}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading && (
          <div className="flex flex-col gap-3" aria-busy>
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-20 animate-pulse rounded-lg border border-[var(--border)] bg-[var(--bg-surface)]/40"
              />
            ))}
          </div>
        )}

        {error && !loading && (
          <div className="flex flex-col gap-3 rounded-lg border border-[var(--trust-anecdotal)]/40 bg-[var(--trust-anecdotal)]/10 p-4">
            <div className="text-[12px] font-medium text-[var(--trust-anecdotal)]">
              Could not reach the search provider
            </div>
            <div className="text-[11px] leading-relaxed text-[var(--text-muted)]">
              {error}
            </div>
            <button
              type="button"
              onClick={onRetry}
              className="self-start rounded-md border border-[var(--border)] bg-transparent px-3 py-1 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
            >
              Retry
            </button>
          </div>
        )}

        {!loading && !error && claims.length === 0 && (
          <div className="flex h-full items-center justify-center text-[12px] text-[var(--text-muted)]">
            No claims returned for this query.
          </div>
        )}

        {!loading && !error && claims.length > 0 && (
          <ul className="flex flex-col gap-3" role="list">
            {claims.map((c, idx) => {
              // Tier authority is the BACKEND: render the tier the claim
              // carries; when it's missing, honestly fall back to ANECDOTAL
              // — the frontend never re-derives tiers client-side.
              const tier = c.tier ?? "ANECDOTAL";
              const tierColor = getTrustTierColor(tier);
              return (
                <li
                  key={`${c.child}-${c.parent_a}-${c.parent_b}-${c.source_url}-${idx}`}
                  className="rounded-lg border border-[var(--border)] bg-[var(--bg-surface)]/60 p-4"
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span
                      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-[0.08em]"
                      style={{
                        borderColor: tierColor,
                        color: tierColor,
                      }}
                    >
                      <span
                        className="h-1.5 w-1.5 rounded-full"
                        style={{ background: tierColor }}
                        aria-hidden
                      />
                      {getTrustTierLabel(tier)}
                    </span>
                    <span className="text-[10px] uppercase tracking-[0.08em] text-[var(--text-faint)]">
                      LINEAGE · {Math.round(c.confidence * 100)}%
                    </span>
                  </div>

                  <div className="mb-2 text-[14px] leading-snug text-[var(--text-primary)]">
                    {claimValue(c, strain)}
                  </div>

                  <div className="mb-3 h-1 w-full overflow-hidden rounded-full bg-[var(--bg-deep)]">
                    <div
                      className="h-full rounded-full"
                      style={{
                        width: `${Math.round(c.confidence * 100)}%`,
                        background: tierColor,
                      }}
                    />
                  </div>

                  <a
                    href={c.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block text-[11px] leading-relaxed text-[var(--text-muted)] hover:text-[var(--accent)]"
                  >
                    <span className="font-medium text-[var(--text-primary)]">
                      {c.source_title || c.source_url}
                    </span>
                    {c.snippet_excerpt ? ` — ${c.snippet_excerpt}` : ""}
                    <span className="ml-1 text-[10px] text-[var(--text-faint)]">
                      · {c.source_engine}
                    </span>
                  </a>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div className="border-t border-[var(--border)] px-6 py-3 text-[10px] uppercase tracking-[0.1em] text-[var(--text-faint)]">
        Pulled from public sources · Stored in knowledge base · Visible on reload
      </div>
    </div>
  );
}