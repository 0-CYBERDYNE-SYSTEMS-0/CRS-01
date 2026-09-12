"use client";

import {
  TRUST_TIER_META,
  getTrustTierColor,
  getTrustTierLabel,
  type TrustTier,
  type NeighborhoodResponse,
} from "@/lib/types";
import { formatDate, relativeTime } from "@/lib/time";

// =============================================================================
// SourcesView — every assertion with its source, as a full-width grid.
// =============================================================================

const BG_DEEP = "#0F2A1F";
const BG_SURFACE = "#1F3A2F";
const BORDER = "#2A4A3A";
const TEXT_PRIMARY = "#EDE6D8";
const TEXT_MUTED = "#8B9A8E";
const TEXT_FAINT = "#5A6B5F";
// Gold accent = verified gold; sourced from the one token table.
const ACCENT = TRUST_TIER_META.VERIFIED.color;
const SERIF = "var(--font-eb-garamond), 'EB Garamond', Georgia, serif";
const SANS = "var(--font-inter), Inter, sans-serif";

export default function SourcesView({
  response,
}: {
  response: NeighborhoodResponse;
}) {
  const center = response.nodes.find((n) => n.id === response.center_node_id);
  const name = center?.data.name ?? center?.label ?? response.slug;

  // Freshness — same rule as the Report masthead: absent timestamps mean
  // the segment is omitted entirely, never rendered as "Unknown".
  const lastResearched = (center?.data.last_researched as number | null | undefined) ?? null;
  const firstSeen = formatDate(center?.data.first_seen as number | null | undefined);
  const freshness: string[] = [];
  if (lastResearched) freshness.push(`Last researched ${relativeTime(lastResearched)}`);
  if (firstSeen) freshness.push(`First seen ${firstSeen}`);

  const claimNodes = response.nodes.filter((n) => n.type === "Claim");
  const lineageEdges = response.edges.filter((e) => e.type === "CHILD_OF");
  const uniqueUrls = new Set<string>();
  for (const e of lineageEdges) {
    for (const u of ((e.data as any)?.sources ?? [])) uniqueUrls.add(u);
  }

  return (
    <div className="h-full overflow-y-auto pane-vignette" style={{ background: BG_DEEP }}>
      <div className="mx-auto max-w-[1120px] px-8 pb-20 pt-8 xl:px-12">
        {/* Header */}
        <div className="mb-8">
          <div className="mb-2 text-[10px] uppercase tracking-[0.22em]" style={{ color: "#8B6914", fontFamily: SANS }}>
            Source Trail
          </div>
          <h2 className="mb-3" style={{ fontFamily: SERIF, fontSize: 32, fontWeight: 500, color: TEXT_PRIMARY, lineHeight: 1.05 }}>
            Sources · <span className="italic">{name}</span>
          </h2>
          <div className="text-[13.5px] italic" style={{ fontFamily: SERIF, color: TEXT_MUTED }}>
            {claimNodes.length} assertion{claimNodes.length === 1 ? "" : "s"} on file ·{" "}
            {lineageEdges.length} lineage edge{lineageEdges.length === 1 ? "" : "s"} ·{" "}
            {uniqueUrls.size} unique source URL{uniqueUrls.size === 1 ? "" : "s"}
          </div>
          {freshness.length > 0 && (
            <div
              className="mt-2 text-[10px] uppercase tracking-[0.16em] tabular-nums"
              style={{ color: TEXT_FAINT, fontFamily: SANS }}
            >
              {freshness.join(" · ")}
            </div>
          )}
        </div>

        {claimNodes.length === 0 && (
          <div style={{ color: TEXT_FAINT, fontSize: 14, fontFamily: SERIF }}>
            No sourced claims recorded for this strain yet.
          </div>
        )}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {claimNodes.map((c) => {
            const tier: TrustTier | undefined = c.data.trust_tier;
            const color = getTrustTierColor(tier);
            const url = (c.data.source_url as string) || "";
            const excerpt = (c.data.excerpt as string) || "";
            const waybackUrl = (c.data.wayback_url as string) || "";
            return (
              <article
                key={c.id}
                className="flex flex-col rounded-xl border p-5"
                style={{ background: BG_SURFACE, borderColor: BORDER }}
              >
                <div className="mb-3 flex items-center gap-2">
                  {tier && (
                    <span
                      className="rounded border px-1.5 py-0.5 text-[8px] uppercase tracking-[0.06em]"
                      style={{ color, borderColor: `${color}60`, fontFamily: SANS }}
                    >
                      {getTrustTierLabel(tier)}
                    </span>
                  )}
                  <span className="text-[9.5px] uppercase tracking-[0.1em]" style={{ color: TEXT_MUTED, fontFamily: SANS }}>
                    {(c.data.type as string) || "LINEAGE"} ·{" "}
                    <span className="tabular-nums">{Math.round((c.data.confidence ?? 0.5) * 100)}%</span>
                  </span>
                </div>
                <div className="mb-2.5" style={{ fontFamily: SERIF, fontSize: 17.5, color: TEXT_PRIMARY, lineHeight: 1.35 }}>
                  {String(c.data.value ?? c.label)}
                </div>
                {excerpt && (
                  <div className="mb-3 text-[12.5px] italic leading-relaxed" style={{ fontFamily: SERIF, color: TEXT_MUTED }}>
                    “{excerpt}”
                  </div>
                )}
                {url ? (
                  <div className="mt-auto flex flex-col gap-1">
                    <a
                      href={url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="break-all text-[11.5px] underline underline-offset-2 hover:opacity-80"
                      style={{ color: ACCENT, fontFamily: SANS }}
                    >
                      {url}
                    </a>
                    {waybackUrl && (
                      <a
                        href={waybackUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="Archived copy captured by the Wayback Machine"
                        className="break-all text-[10px] underline underline-offset-2 hover:opacity-80"
                        style={{ color: TEXT_MUTED, fontFamily: SANS }}
                      >
                        Archived copy ↗
                      </a>
                    )}
                  </div>
                ) : (
                  <span className="mt-auto text-[11px]" style={{ color: TEXT_FAINT, fontFamily: SANS }}>
                    No source URL recorded
                  </span>
                )}
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}
