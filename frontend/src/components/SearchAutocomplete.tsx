"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { searchStrains } from "@/lib/api-client";
import type { StrainSearchMatch } from "@/lib/api-client";
import { getTrustTierColor } from "@/lib/types";

// ---------------------------------------------------------------------------
// Debounce: wait this long after the user stops typing before hitting the
// search endpoint. 150ms is fast enough to feel live, slow enough to avoid
// firing on every keystroke.
// ---------------------------------------------------------------------------
const DEBOUNCE_MS = 150;

interface SearchAutocompleteProps {
  value: string;
  onChange: (next: string) => void;
  onSubmit: (value: string) => void;
  onSelectMatch: (match: StrainSearchMatch) => void;
  placeholder?: string;
}

export default function SearchAutocomplete({
  value,
  onChange,
  onSubmit,
  onSelectMatch,
  placeholder,
}: SearchAutocompleteProps) {
  const [matches, setMatches] = useState<StrainSearchMatch[]>([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState<number>(-1);
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastQueryRef = useRef<string>("");

  // ── Debounced search whenever the input changes ─────────────────────
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = value.trim();
    if (q.length === 0) {
      setMatches([]);
      setLoading(false);
      return;
    }
    debounceRef.current = setTimeout(() => {
      if (q === lastQueryRef.current) return;
      lastQueryRef.current = q;
      setLoading(true);
      searchStrains(q, 6)
        .then((r) => {
          setMatches(r.matches);
          setActiveIndex(r.matches.length > 0 ? 0 : -1);
        })
        .catch(() => {
          setMatches([]);
          setActiveIndex(-1);
        })
        .finally(() => setLoading(false));
    }, DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [value]);

  // ── Close dropdown when the input loses focus ───────────────────────
  useEffect(() => {
    function onDocClick(e: MouseEvent) {
      if (!wrapRef.current) return;
      if (!wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        setOpen(true);
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => Math.min(matches.length - 1, i + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) => Math.max(0, i - 1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (activeIndex >= 0 && matches[activeIndex]) {
          onSelectMatch(matches[activeIndex]);
          setOpen(false);
        } else {
          onSubmit(value);
          setOpen(false);
        }
      } else if (e.key === "Escape") {
        setOpen(false);
      }
    },
    [open, matches, activeIndex, onSubmit, onSelectMatch, value]
  );

  return (
    <div ref={wrapRef} className="relative">
      <input
        type="text"
        placeholder={placeholder ?? "Search…"}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        role="combobox"
        aria-expanded={open}
        aria-controls="search-autocomplete-listbox"
        aria-autocomplete="list"
        aria-activedescendant={
          activeIndex >= 0 ? `ac-item-${activeIndex}` : undefined
        }
        className="w-[280px] rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] px-3.5 py-2 text-sm text-[var(--text-primary)] outline-none transition-[border-color] duration-200 placeholder:text-[var(--text-muted)] focus:border-[var(--accent)]"
      />

      {open && value.trim().length > 0 && (
        <div
          id="search-autocomplete-listbox"
          role="listbox"
          className="absolute left-0 right-0 top-full z-50 mt-1 max-h-[320px] overflow-y-auto rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] shadow-2xl"
        >
          {loading && matches.length === 0 && (
            <div className="px-3 py-2 text-xs text-[var(--text-muted)]">
              Searching…
            </div>
          )}

          {!loading && matches.length === 0 && (
            <button
              type="button"
              role="option"
              aria-selected="false"
              onClick={() => {
                onSubmit(value);
                setOpen(false);
              }}
              className="flex w-full items-center justify-between px-3 py-2 text-left text-xs hover:bg-[var(--bg-deep)]"
            >
              <span className="text-[var(--text-muted)]">
                No matches for <span className="text-[var(--text-primary)]">"{value}"</span>
              </span>
              <span className="text-[10px] uppercase tracking-[0.08em] text-[var(--accent)]">
                Try anyway →
              </span>
            </button>
          )}

          {matches.map((m, i) => (
            <button
              key={m.slug}
              id={`ac-item-${i}`}
              type="button"
              role="option"
              aria-selected={i === activeIndex}
              onMouseEnter={() => setActiveIndex(i)}
              onClick={() => {
                onSelectMatch(m);
                setOpen(false);
              }}
              className={`flex w-full items-center justify-between border-t border-[var(--border)] px-3 py-2 text-left text-xs first:border-t-0 ${
                i === activeIndex
                  ? "bg-[var(--bg-deep)]"
                  : "hover:bg-[var(--bg-deep)]"
              }`}
            >
              <span className="flex flex-col">
                <span className="text-[13px] text-[var(--text-primary)]">
                  {m.name}
                </span>
                <span className="text-[10px] uppercase tracking-[0.06em] text-[var(--text-muted)]">
                  {m.slug}
                </span>
              </span>
              <span className="flex items-center gap-2">
                <span className="font-mono text-[10px] text-[var(--text-muted)]">
                  {Math.round(m.confidence * 100)}%
                </span>
                <span
                  className="h-1.5 w-1.5 rounded-full"
                  style={{ background: getTrustTierColor(m.trust_tier) }}
                  aria-label={m.trust_tier}
                />
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}