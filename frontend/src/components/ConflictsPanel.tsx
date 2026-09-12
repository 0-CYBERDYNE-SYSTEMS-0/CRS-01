"use client";

import { useCallback, useEffect, useState } from "react";

import { fetchConflicts, quarantineObservation } from "@/lib/api-client";
import { formatDate } from "@/lib/time";
import {
  getTrustTierColor,
  sourceDomain,
  type ConflictsResponse,
  type StrainConflict,
  type ConflictTuple,
} from "@/lib/types";

// =============================================================================
// ConflictsPanel — WHO says WHAT, side by side. Renders the derived
// conflicting parent-set assertions for a strain: each side of a dispute
// ("Source A says GSC × OG Kush / Source B says GSC × Chemdawg 91") with
// its own expandable source list. CONTRADICTED accent comes from
// getTrustTierColor — the single tier-color source.
//
// Curation loop: each source observation can be quarantined by a human.
// Quarantine is a flag, not a deletion — the raw row stays on file — but
// it is excluded from every consensus computation, so tiers re-derive
// without it.
// =============================================================================

const CONTRADICTED = getTrustTierColor("CONTRADICTED");
const BG_DEEP = "#0F2A1F";
const TEXT_PRIMARY = "#EDE6D8";
const TEXT_MUTED = "#8B9A8E";
const TEXT_FAINT = "#5A6B5F";

export default function ConflictsPanel({
  slug,
  strainName,
  onDataChanged,
}: {
  slug: string;
  strainName?: string;
  onDataChanged?: () => void;
}) {
  const [data, setData] = useState<ConflictsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    fetchConflicts(slug)
      .then((r) => setData(r))
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : "Failed to load conflicts");
      })
      .finally(() => setLoading(false));
  }, [slug]);

  useEffect(() => {
    setData(null);
    setOpenKey(null);
    setNotice(null);
    load();
  }, [slug, load]);

  // After a quarantine the derived state changed everywhere: refetch the
  // conflicts and let the host refresh the neighborhood/graph so the
  // recomputed tiers are visible.
  const handleQuarantined = useCallback(() => {
    load();
    onDataChanged?.();
  }, [load, onDataChanged]);

  if (loading && !data) {
    return (
      <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-surface)]/40 p-3.5">
        <div className="mb-1 text-[9px] uppercase tracking-[0.14em]" style={{ color: TEXT_FAINT }}>
          Conflicts
        </div>
        <span className="flex items-center gap-2 text-[11.5px]" style={{ color: TEXT_MUTED }}>
          <span
            className="h-3 w-3 animate-spin rounded-full border-2 border-t-transparent"
            style={{ borderColor: `${CONTRADICTED} transparent ${CONTRADICTED} transparent` }}
          />
          Deriving disputes from raw evidence…
        </span>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className="rounded-xl border p-3.5 text-[11.5px]"
        style={{ borderColor: `${CONTRADICTED}55`, background: "rgba(239,68,68,0.06)", color: TEXT_MUTED }}
      >
        Could not load conflicts: {error}
      </div>
    );
  }

  // Clean strain — nothing to advertise.
  if (!data || data.conflictCount === 0 || data.conflicts.length === 0) return null;

  return (
    <div className="flex flex-col gap-3">
      {notice && (
        <div
          className="rounded-lg border px-2.5 py-1.5 text-[10.5px]"
          style={{ borderColor: `${CONTRADICTED}44`, background: "rgba(239,68,68,0.07)", color: TEXT_MUTED }}
        >
          {notice}
        </div>
      )}
      {data.conflicts.map((conflict, ci) => (
        <ConflictCard
          key={`${conflict.child}-${ci}`}
          conflict={conflict}
          index={ci}
          openKey={openKey}
          onToggle={setOpenKey}
          fallbackName={strainName}
          onQuarantined={(url) => {
            setNotice(
              `Quarantined ${sourceDomain(url)} — excluded from all consensus math; tiers re-derived.`
            );
            handleQuarantined();
          }}
        />
      ))}
      <div className="text-[9.5px] leading-relaxed" style={{ color: TEXT_FAINT }}>
        Quarantining an observation excludes it from all consensus math — the raw
        row stays on file, and a curator can restore it.
      </div>
    </div>
  );
}

function ConflictCard({
  conflict,
  index,
  openKey,
  onToggle,
  fallbackName,
  onQuarantined,
}: {
  conflict: StrainConflict;
  index: number;
  openKey: string | null;
  onToggle: (key: string | null) => void;
  fallbackName?: string;
  onQuarantined: (sourceUrl: string) => void;
}) {
  const name = conflict.childName || fallbackName || conflict.child;
  const sideBySide = conflict.tuples.length === 2;

  return (
    <div
      className="rounded-xl border p-3.5"
      style={{ borderColor: `${CONTRADICTED}44`, background: "rgba(239,68,68,0.05)" }}
    >
      {/* Header */}
      <div className="mb-2.5">
        <div className="mb-1 flex items-center gap-1.5">
          <span
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{ background: CONTRADICTED }}
            aria-hidden
          />
          <span
            className="text-[8px] uppercase tracking-[0.14em]"
            style={{ color: CONTRADICTED }}
          >
            Contradicted
          </span>
        </div>
        <div
          className="text-[11.5px] leading-snug"
          style={{ color: TEXT_PRIMARY, fontFamily: "var(--font-eb-garamond), 'EB Garamond', serif", fontStyle: "italic" }}
        >
          {conflict.summary || `${name} has conflicting parent-strain assertions`}
        </div>
      </div>

      {/* The sides — two tuples sit side by side, more stack */}
      {sideBySide ? (
        <div className="relative grid grid-cols-2 gap-2">
          {conflict.tuples.map((t, ti) => (
            <TupleCell
              key={t.parents.join("|")}
              tuple={t}
              childSlug={conflict.child}
              open={openKey === `${index}-${ti}`}
              onToggle={() =>
                onToggle(openKey === `${index}-${ti}` ? null : `${index}-${ti}`)
              }
              toggleKey={`${index}-${ti}`}
              onQuarantined={onQuarantined}
            />
          ))}
          <span
            className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border px-1.5 py-0.5 text-[7.5px] uppercase tracking-[0.1em]"
            style={{ borderColor: `${CONTRADICTED}66`, color: CONTRADICTED, background: BG_DEEP }}
          >
            vs
          </span>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {conflict.tuples.map((t, ti) => (
            <TupleCell
              key={t.parents.join("|")}
              tuple={t}
              childSlug={conflict.child}
              open={openKey === `${index}-${ti}`}
              onToggle={() =>
                onToggle(openKey === `${index}-${ti}` ? null : `${index}-${ti}`)
              }
              toggleKey={`${index}-${ti}`}
              onQuarantined={onQuarantined}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function TupleCell({
  tuple,
  childSlug,
  open,
  onToggle,
  toggleKey,
  onQuarantined,
}: {
  tuple: ConflictTuple;
  childSlug: string;
  open: boolean;
  onToggle: () => void;
  toggleKey: string;
  onQuarantined: (sourceUrl: string) => void;
}) {
  const count = tuple.sources.length;
  // A source asserting this tuple contributed an observation for EACH parent
  // in it — quarantining the source quarantines all of them.
  const [busyUrl, setBusyUrl] = useState<string | null>(null);
  const [quarantineError, setQuarantineError] = useState<string | null>(null);

  const handleQuarantine = async (sourceUrl: string) => {
    if (
      !window.confirm(
        "Quarantine this observation? It stays on file but is excluded from all consensus math, and tiers re-derive without it."
      )
    ) {
      return;
    }
    setBusyUrl(sourceUrl);
    setQuarantineError(null);
    try {
      await Promise.all(
        tuple.parents.map((p) =>
          quarantineObservation(childSlug, p, sourceUrl, true)
        )
      );
      onQuarantined(sourceUrl);
    } catch (e: unknown) {
      setQuarantineError(
        e instanceof Error ? e.message : "Quarantine failed"
      );
    } finally {
      setBusyUrl(null);
    }
  };

  return (
    <div
      className="rounded-lg border px-2.5 py-2"
      style={{ borderColor: `${CONTRADICTED}33`, background: "rgba(15,42,31,0.6)" }}
    >
      <div
        className="text-[12.5px] leading-tight"
        style={{ color: TEXT_PRIMARY, fontFamily: "var(--font-eb-garamond), 'EB Garamond', serif", fontStyle: "italic" }}
      >
        {(tuple.parentNames.length ? tuple.parentNames : tuple.parents).join(" × ")}
      </div>
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="mt-1.5 cursor-pointer text-[9px] uppercase tracking-[0.08em] underline underline-offset-2 transition-colors"
        style={{ color: TEXT_MUTED, background: "none", border: "none", padding: 0 }}
        onMouseEnter={(e) => { e.currentTarget.style.color = CONTRADICTED; }}
        onMouseLeave={(e) => { e.currentTarget.style.color = TEXT_MUTED; }}
      >
        {open ? "Hide sources" : `${count} source${count === 1 ? "" : "s"} ↓`}
      </button>

      {open && (
        <div className="mt-2 flex flex-col gap-2 border-t pt-2" style={{ borderColor: `${CONTRADICTED}22` }}>
          {tuple.sources.map((s) => (
            <div key={s.url}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="truncate text-[10.5px]" style={{ color: TEXT_PRIMARY }} title={s.title || s.url}>
                  {s.title || s.url}
                </span>
                <span className="shrink-0 text-[8px] uppercase tracking-[0.06em]" style={{ color: TEXT_FAINT }}>
                  {[s.engine, formatDate(s.observedAt)].filter(Boolean).join(" · ")}
                </span>
              </div>
              <div className="mt-0.5 flex items-center justify-between gap-2">
                <a
                  href={s.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="truncate text-[9.5px] underline underline-offset-2"
                  style={{ color: CONTRADICTED }}
                >
                  {s.url}
                </a>
                <span className="flex shrink-0 items-center gap-1.5">
                  <span
                    className="rounded border px-1 py-px text-[7.5px] tracking-[0.04em]"
                    style={{ color: TEXT_MUTED, borderColor: "var(--border)" }}
                  >
                    {sourceDomain(s.url)}
                  </span>
                  <button
                    type="button"
                    onClick={() => handleQuarantine(s.url)}
                    disabled={busyUrl !== null}
                    title="Quarantine this observation — excluded from all consensus math"
                    className="cursor-pointer rounded border px-1.5 py-px text-[7.5px] uppercase tracking-[0.06em] transition-colors disabled:opacity-50"
                    style={{
                      color: busyUrl === s.url ? TEXT_FAINT : TEXT_MUTED,
                      borderColor: "var(--border)",
                      background: "transparent",
                    }}
                    onMouseEnter={(e) => {
                      if (busyUrl === null) e.currentTarget.style.color = CONTRADICTED;
                    }}
                    onMouseLeave={(e) => {
                      e.currentTarget.style.color = TEXT_MUTED;
                    }}
                  >
                    {busyUrl === s.url ? "…" : "Quarantine"}
                  </button>
                </span>
              </div>
            </div>
          ))}
          {quarantineError && (
            <div className="text-[9.5px]" style={{ color: CONTRADICTED }}>
              {quarantineError}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
