"use client";

import type { StrainListItem } from "@/lib/api-client";
import { getTrustTierColor } from "@/lib/types";

interface SuggestionChipsProps {
  suggestions: StrainListItem[];
  onSelect: (slug: string) => void;
  activeSlug?: string;
}

/** Compact inline chip row for the workspace toolbar. */
export default function SuggestionChips({
  suggestions,
  onSelect,
  activeSlug,
}: SuggestionChipsProps) {
  if (suggestions.length === 0) return null;

  return (
    <div className="flex items-center gap-2">
      <span className="hidden text-[9px] uppercase tracking-[0.16em] text-[var(--text-faint)] xl:inline">
        Try
      </span>
      <div className="flex items-center gap-1.5">
        {suggestions.slice(0, 5).map((s) => {
          const color = getTrustTierColor(s.trust_tier);
          const isActive = s.slug === activeSlug;
          return (
            <button
              key={s.slug}
              type="button"
              onClick={() => onSelect(s.slug)}
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10.5px] transition-colors ${
                isActive
                  ? "border-[var(--accent)] bg-[var(--bg-surface)] text-[var(--text-primary)]"
                  : "border-[var(--border)] bg-transparent text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
              }`}
            >
              <span
                className="h-1.5 w-1.5 rounded-full"
                style={{ background: color }}
                aria-hidden
              />
              <span className="max-w-[110px] truncate">{s.name}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
