"use client";

import { getTrustTierColor } from "@/lib/types";

type SuggestionShape = {
  slug: string;
  name: string;
  confidence?: number;
  trust_tier?: string;
};

interface EmptyStateProps {
  strain: string;
  onRetry: () => void;
  onResearch?: () => void;
  suggestions?: SuggestionShape[] | null;
  onSuggestion?: (slug: string) => void;
}

export default function EmptyState({
  strain,
  onRetry,
  onResearch,
  suggestions,
  onSuggestion,
}: EmptyStateProps) {
  return (
    <div
      className="flex h-full w-full flex-col items-center justify-center gap-6 px-8 text-center"
      style={{ background: "var(--bg-deep)" }}
    >
      <svg
        width="96"
        height="96"
        viewBox="0 0 96 96"
        fill="none"
        className="text-[var(--text-faint)]"
        aria-hidden
      >
        <circle
          cx="48" cy="48" r="40"
          stroke="currentColor" strokeOpacity="0.25" strokeDasharray="2 4"
        />
        <circle
          cx="48" cy="48" r="26"
          stroke="currentColor" strokeOpacity="0.4" strokeDasharray="2 4"
        />
        <circle cx="48" cy="48" r="6" fill="currentColor" opacity="0.5" />
      </svg>

      <div className="max-w-md">
        <div className="mb-2 text-[10px] uppercase tracking-[0.16em] text-[var(--text-faint)]">
          No data in the knowledge base
        </div>
        <h2 className="mb-2 text-[20px] font-medium text-[var(--text-primary)]">
          We don&apos;t have a record for{" "}
          <span className="italic" style={{ color: "var(--accent)" }}>
            {strain}
          </span>
        </h2>
        <p className="text-sm leading-relaxed text-[var(--text-muted)]">
          {suggestions && suggestions.length > 0
            ? "The knowledge base suggests similar strains below. Or click Research to scan the open web for lineage data."
            : "This strain isn't in the knowledge base yet. Click Research to scan the open web — claims persist to the cache so the next visit sees them instantly."}
        </p>
      </div>

      <div className="flex flex-col items-center gap-2">
        {suggestions && suggestions.length > 0 && onSuggestion && (
          <div className="flex flex-wrap justify-center gap-1.5">
            {suggestions.slice(0, 6).map((s) => {
              const color = getTrustTierColor(
                (s.trust_tier as any) ?? "ANECDOTAL"
              );
              return (
                <button
                  key={s.slug}
                  type="button"
                  onClick={() => onSuggestion(s.slug)}
                  className="flex items-center gap-1.5 rounded-full border border-[var(--border)] bg-transparent px-3 py-1.5 text-[11px] text-[var(--text-muted)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
                >
                  <span
                    className="h-1.5 w-1.5 rounded-full"
                    style={{ background: color }}
                    aria-hidden
                  />
                  {s.name}
                </button>
              );
            })}
          </div>
        )}

        <div className="mt-2 flex flex-wrap items-center justify-center gap-2">
          {onResearch && (
            <button
              type="button"
              onClick={onResearch}
              className="flex items-center gap-2 rounded-lg border border-[var(--accent)] bg-[var(--accent)]/10 px-3.5 py-1.5 text-[11px] uppercase tracking-[0.08em] text-[var(--accent)] transition-colors hover:bg-[var(--accent)]/20"
            >
              Research on the open web →
            </button>
          )}
          <button
            type="button"
            onClick={onRetry}
            className="rounded-lg border border-[var(--border)] bg-transparent px-3 py-1.5 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
          >
            Try again
          </button>
        </div>
      </div>
    </div>
  );
}