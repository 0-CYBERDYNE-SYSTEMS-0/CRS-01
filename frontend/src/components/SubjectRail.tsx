"use client";

import { WHEEL_CLAIM_LIMIT } from "@/components/GraphCanvas";
import ConflictsPanel from "@/components/ConflictsPanel";
import {
  getTrustTierColor,
  type NeighborhoodResponse,
} from "@/lib/types";

// =============================================================================
// SubjectRail — context rail shown when nothing on the wheel is selected:
// the subject's identity, confidence, claims-on-file count, top claims,
// and (when they exist) the conflict drill-down.
// =============================================================================

export default function SubjectRail({
  response,
  strain,
  researching,
  claimCount,
  claimSource,
  onOpenReport,
  onReview,
  onDeepen,
  onSelect,
  onDataChanged,
}: {
  response: NeighborhoodResponse;
  strain: string;
  researching: boolean;
  claimCount: number;
  claimSource: string;
  onOpenReport: () => void;
  onReview: () => void;
  onDeepen: () => void;
  onSelect: (id: string) => void;
  onDataChanged?: () => void;
}) {
  const center = response.nodes.find((n) => n.id === response.center_node_id);
  const name = center?.data.name ?? center?.label ?? strain;
  const tier = center?.data.trust_tier as SubjectRailTier | undefined;
  const color = tier ? getTrustTierColor(tier) : "var(--accent)";
  const conf = Math.round((center?.data.confidence ?? 0.5) * 100);
  const origin = (center?.data.origin as string) || "";
  const imageUrl = (center?.data.image_url as string) || "";
  const summary = (center?.data.summary as string) || "";

  const engines = new Set<string>();
  for (const e of response.edges) {
    if ((e.data as any)?.source_domains) {
      for (const d of (e.data as any).source_domains) engines.add(d);
    }
  }
  // Audit drill-down: when the subject's verdict is CONTRADICTED — or any
  // edge disagrees — expose WHO says WHAT straight in the rail.
  const contradicted = tier === "CONTRADICTED";
  const hasDisagreements = response.edges.some(
    (e) => (e.data as any)?.agreement === "disagreement"
  );
  const allClaims = response.nodes.filter((n) => n.type === "Claim");
  const totalClaimCount = allClaims.length;
  const topClaims = [...allClaims]
    .sort(
      (a, b) =>
        (b.data.confidence ?? 0.5) - (a.data.confidence ?? 0.5) ||
        a.id.localeCompare(b.id)
    )
    .slice(0, WHEEL_CLAIM_LIMIT);

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      <div className="border-b border-[var(--border)] px-5 py-3">
        <div className="text-[9px] uppercase tracking-[0.18em] text-[var(--text-faint)]">
          Subject
        </div>
      </div>

      <div className="flex-1 px-5 py-4">
        {/* Identity */}
        {imageUrl && (
          <img
            src={imageUrl}
            alt={name}
            referrerPolicy="no-referrer"
            className="mb-3 h-[140px] w-full rounded-xl border border-[var(--border)] object-cover"
          />
        )}
        <div className="mb-1 flex flex-wrap items-center gap-2">
          {tier && (
            <span
              className="rounded border px-1.5 py-0.5 text-[8px] uppercase tracking-[0.06em]"
              style={{ color, borderColor: `${tier ? getTrustTierColor(tier) : ""}60` }}
            >
              {tier ? tier.replace("_", " ").toLowerCase() : ""}
            </span>
          )}
          {origin && (
            <span className="rounded border border-[var(--accent)]/40 px-1.5 py-0.5 text-[8px] uppercase tracking-[0.06em] text-[var(--accent)]">
              {origin}
            </span>
          )}
        </div>
        <h2 className="font-serif mb-2 text-[30px] italic leading-[1.05] text-[var(--text-primary)]">
          {name}
        </h2>

        {/* Confidence */}
        <div className="mb-4 flex items-center gap-2">
          <div className="h-1 flex-1 overflow-hidden rounded-full bg-[var(--bg-deep)]">
            <div
              className="h-full rounded-full"
              style={{ width: `${conf}%`, background: color }}
            />
          </div>
          <span className="text-[11px] tabular-nums text-[var(--text-muted)]">{conf}%</span>
        </div>

        {summary && (
          <p className="mb-4 text-[12px] leading-relaxed text-[var(--text-muted)]">
            {summary}
          </p>
        )}

        {/* Meta grid */}
        <div className="mb-4 grid grid-cols-3 gap-2">
          <RailStat label="Nodes" value={String(response.stats.node_count)} />
          <RailStat label="Depth" value={String(response.depth)} />
          <RailStat label="Domains" value={String(engines.size)} />
        </div>

        {/* Actions */}
        <div className="mb-4 flex flex-col gap-2">
          <button
            type="button"
            onClick={onOpenReport}
            className="w-full cursor-pointer rounded-lg py-2.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#0C120E] transition-opacity hover:opacity-90"
            style={{ background: "var(--accent)" }}
          >
            View full dossier →
          </button>
          <button
            type="button"
            onClick={onDeepen}
            disabled={researching}
            className="w-full cursor-pointer rounded-lg border border-[var(--border)] py-2 text-[10px] uppercase tracking-[0.12em] text-[var(--text-muted)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)] disabled:opacity-50"
          >
            {researching ? "Researching…" : "Deepen research"}
          </button>
        </div>

        {/* Claims on file */}
        <div className="rounded-xl border border-[var(--border)] bg-[var(--bg-surface)]/40 p-3.5">
          <div className="mb-1 flex items-center justify-between gap-2">
            <span className="text-[9px] uppercase tracking-[0.14em] text-[var(--text-faint)]">
              Research claims
            </span>
            {!researching && claimCount > 0 && (
              <button
                type="button"
                onClick={onReview}
                className="cursor-pointer rounded-md border border-[var(--border)] px-2 py-0.5 text-[9.5px] uppercase tracking-[0.08em] text-[var(--text-muted)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
              >
                Review
              </button>
            )}
          </div>
          {researching ? (
            <span className="flex items-center gap-2 text-[11.5px] text-[var(--text-muted)]">
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
              Searching the open web…
            </span>
          ) : claimCount > 0 ? (
            <span className="text-[11.5px] text-[var(--text-muted)]">
              {claimCount} claim{claimCount === 1 ? "" : "s"} on file · {claimSource}
            </span>
          ) : (
            <span className="text-[11.5px] text-[var(--text-faint)]">
              No web research runs yet for this strain.
            </span>
          )}
        </div>

        {/* Top claims — mirrors what the wheel shows */}
        {topClaims.length > 0 && (
          <div className="mt-4">
            <div className="mb-2 flex items-baseline justify-between">
              <span className="text-[9px] uppercase tracking-[0.14em] text-[var(--text-faint)]">
                Top claims
              </span>
              {totalClaimCount > topClaims.length && (
                <span className="text-[9px] text-[var(--text-faint)]">
                  +{totalClaimCount - topClaims.length} more
                </span>
              )}
            </div>
            <div className="flex flex-col gap-1.5">
              {topClaims.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => onSelect(c.id)}
                  className="cursor-pointer rounded-lg border border-[var(--border)] bg-transparent px-3 py-1.5 text-left text-[11px] text-[var(--text-muted)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
                >
                  <span
                    className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full align-middle"
                    style={{ background: getTrustTierColor(c.data.trust_tier) }}
                  />
                  {String(c.data.value ?? c.label).slice(0, 44)}
                  {String(c.data.value ?? c.label).length > 44 ? "…" : ""}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Conflicts — the evidence behind a red verdict, auditable in place */}
        {(contradicted || hasDisagreements) && (
          <div className="mt-4">
            <div className="mb-2 text-[9px] uppercase tracking-[0.14em] text-[var(--text-faint)]">
              Conflicts
            </div>
            <ConflictsPanel
              slug={response.slug}
              strainName={name}
              onDataChanged={onDataChanged}
            />
          </div>
        )}
      </div>

      <div className="border-t border-[var(--border)] px-5 py-3 text-[9px] uppercase tracking-[0.12em] text-[var(--text-faint)]">
        Phase 2 prototype · public-source only
      </div>
    </div>
  );
}

type SubjectRailTier = Parameters<typeof getTrustTierColor>[0];

function RailStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-surface)]/40 px-2.5 py-2 text-center">
      <div className="font-serif text-[20px] leading-none text-[var(--text-primary)] tabular-nums">
        {value}
      </div>
      <div className="mt-1 text-[8px] uppercase tracking-[0.14em] text-[var(--text-faint)]">
        {label}
      </div>
    </div>
  );
}
