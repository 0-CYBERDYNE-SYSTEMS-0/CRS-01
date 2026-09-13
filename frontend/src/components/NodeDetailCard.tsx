"use client";

import { useState } from "react";

import { fetchEdgeEvidence } from "@/lib/api-client";
import { relativeTime } from "@/lib/time";
import {
  TRUST_TIER_META,
  getTrustTierColor,
  getTrustTierLabel,
  nodeKindLabel,
  sourceDomain,
  type TrustTier,
  type NeighborhoodNode,
  type NeighborhoodResponse,
  type EvidenceResponse,
} from "@/lib/types";

// =============================================================================
// NodeDetailCard — click-to-open provenance panel. Lives in the page's
// context rail (right side of the graph view) rather than floating over
// the wheel, so it never covers the graph.
// =============================================================================

function parentRoleLabel(
  response: NeighborhoodResponse,
  nodeId: string
): string | null {
  const edge = response.edges.find(
    (e) =>
      e.type === "CHILD_OF" &&
      ((e.source === response.center_node_id && e.target === nodeId) ||
        (e.target === response.center_node_id && e.source === nodeId))
  );
  const role = edge?.data?.role;
  if (role === "female" || role === "male") return role;
  return null;
}

const BORDER = "#2A4A3A";
const TEXT_PRIMARY = "#EDE6D8";
const TEXT_MUTED = "#8B9A8E";
const TEXT_FAINT = "#5A6B5F";
// Gold accent = verified gold; sourced from the one token table.
const ACCENT = TRUST_TIER_META.VERIFIED.color;
const BG_DEEP = "#0F2A1F";

export default function NodeDetailCard({
  node,
  response,
  edgeCount,
  onClose,
  onOpenReport,
}: {
  node: NeighborhoodNode;
  response: NeighborhoodResponse;
  edgeCount: number;
  onClose: () => void;
  onOpenReport?: () => void;
}) {
  const tier: TrustTier | undefined = node.data.trust_tier;
  const color = getTrustTierColor(tier);
  const name = String(node.data.name ?? node.data.handle ?? node.label ?? node.id);
  const conf = Math.round((node.data.confidence ?? 0.5) * 100);
  const kind = nodeKindLabel(node);
  const rel = (node.data.relation as string) || "related";
  // Evidence drill-down is offered for strain satellites with a real lineage
  // relation to the subject (the subject itself and loose "related" nodes
  // have no single edge to drill into).
  const isLineageSatellite =
    node.type === "Strain" && rel !== "subject" && LINEAGE_RELATIONS.has(rel);
  const origin = (node.data.origin as string) || "";
  const imageUrl = (node.data.image_url as string) || "";
  const summary = (node.data.summary as string) || "";
  const excerpt = (node.data.excerpt as string) || "";
  const sourceUrl = (node.data.source_url as string) || "";
  const props = (node.data.props as Record<string, unknown>) || {};

  // Full provenance: every source is reachable — 3 inline, rest behind a toggle.
  const [showAllSources, setShowAllSources] = useState(false);

  const edgeSources = response.edges
    .filter((e) => e.source === node.id || e.target === node.id)
    .flatMap((e) => e.data?.sources ?? []);
  const uniqueSources = Array.from(new Set(edgeSources));
  const visibleSources = showAllSources ? uniqueSources : uniqueSources.slice(0, 3);

  return (
    <div className="flex h-full flex-col">
      {/* Header row */}
      <div className="flex items-center justify-between gap-2 border-b border-[var(--border)] px-5 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[8.5px] font-semibold uppercase tracking-[0.14em] text-[var(--text-muted)]">
            {kind}
          </span>
          <span className="text-[8.5px] uppercase tracking-[0.08em] text-[var(--text-faint)]">
            {rel}
          </span>
          {tier && (
            <span
              className="rounded border px-1.5 py-0.5 text-[8px] uppercase tracking-[0.06em]"
              style={{ color, borderColor: `${color}60` }}
            >
              {getTrustTierLabel(tier)}
            </span>
          )}
          {origin && (
            <span
              className="rounded border px-1.5 py-0.5 text-[8px] uppercase tracking-[0.06em]"
              style={{ color: ACCENT, borderColor: `${ACCENT}60` }}
            >
              {origin}
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          aria-label="Close detail"
          className="cursor-pointer rounded-md border border-[var(--border)] px-2 py-0.5 text-[13px] leading-tight text-[var(--text-muted)] transition-colors hover:text-[var(--text-primary)]"
        >
          ✕
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-5 py-4" style={{ fontFamily: "Inter, sans-serif", color: TEXT_PRIMARY }}>
        {imageUrl && (
          <img
            src={imageUrl}
            alt={name}
            referrerPolicy="no-referrer"
            style={{
              width: "100%",
              height: 130,
              objectFit: "cover",
              borderRadius: 10,
              border: `1px solid ${BORDER}`,
              marginBottom: 12,
            }}
          />
        )}
        <div
          className="mb-2.5"
          style={{ fontFamily: "var(--font-eb-garamond), 'EB Garamond', serif", fontSize: 26, fontStyle: "italic", fontWeight: 500, lineHeight: 1.1 }}
        >
          {name}
        </div>
        {((node.data.aliases as string[] | undefined) ?? []).length > 0 && (
          <div className="mb-3 flex flex-wrap gap-1">
            {((node.data.aliases as string[]) ?? []).map((alias) => (
              <span
                key={alias}
                className="rounded border px-1.5 py-0.5 text-[8px] uppercase tracking-[0.08em]"
                style={{ color: TEXT_FAINT, borderColor: BORDER }}
              >
                {alias}
              </span>
            ))}
          </div>
        )}

        {/* Confidence bar */}
        <div className="mb-4 flex items-center gap-2">
          <div style={{ flex: 1, height: 4, background: BORDER, borderRadius: 2, overflow: "hidden" }}>
            <div style={{ width: `${conf}%`, height: "100%", background: color, borderRadius: 2 }} />
          </div>
          <span style={{ fontSize: 11, color: TEXT_MUTED, fontVariantNumeric: "tabular-nums" }}>{conf}%</span>
        </div>

        {/* Metadata grid */}
        <div className="mb-4 grid grid-cols-2 gap-2">
          <MetaCell
            label="Relation"
            value={parentRoleLabel(response, node.id) ?? rel}
          />
          <MetaCell label="Connections" value={String(edgeCount)} />
          {props.thc_range ? <MetaCell label="THC" value={String(props.thc_range)} /> : null}
          {props.type ? <MetaCell label="Type" value={String(props.type)} /> : null}
          {props.breeder ? <MetaCell label="Breeder" value={String(props.breeder)} /> : null}
          {node.data.breeder ? <MetaCell label="Breeder" value={String(node.data.breeder)} /> : null}
        </div>

        {/* Summary / claim text */}
        {summary && (
          <div style={{ fontSize: 12, lineHeight: 1.6, color: TEXT_MUTED, marginBottom: 12 }}>
            {summary}
          </div>
        )}
        {excerpt && (
          <div style={{ fontSize: 11.5, lineHeight: 1.55, color: TEXT_MUTED, fontStyle: "italic", marginBottom: 12 }}>
            “{excerpt}”
          </div>
        )}

        {/* Sources */}
        {uniqueSources.length > 0 && (
          <div className="mb-4">
            <div className="mb-1.5 text-[8px] uppercase tracking-[0.14em]" style={{ color: TEXT_FAINT }}>
              Sources
            </div>
            {visibleSources.map((u) => {
              const domain = sourceDomain(u);
              return (
                <div key={u} className="mb-1.5 flex items-start justify-between gap-2">
                  <a
                    href={u}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      flex: 1,
                      fontSize: 10.5,
                      color: ACCENT,
                      textDecoration: "underline",
                      wordBreak: "break-all",
                    }}
                  >
                    {u}
                  </a>
                  {domain && (
                    <span
                      className="shrink-0 rounded border px-1.5 py-0.5 text-[8px] tracking-[0.04em]"
                      style={{ color: TEXT_MUTED, borderColor: BORDER }}
                      title={`Source domain: ${domain}`}
                    >
                      {domain}
                    </span>
                  )}
                </div>
              );
            })}
            {uniqueSources.length > 3 && (
              <button
                type="button"
                onClick={() => setShowAllSources((v) => !v)}
                className="cursor-pointer text-[9.5px] uppercase tracking-[0.08em] underline underline-offset-2 transition-colors"
                style={{ color: TEXT_MUTED, background: "none", border: "none", padding: 0 }}
                onMouseEnter={(e) => { e.currentTarget.style.color = ACCENT; }}
                onMouseLeave={(e) => { e.currentTarget.style.color = TEXT_MUTED; }}
              >
                {showAllSources
                  ? "Show fewer"
                  : `Show all ${uniqueSources.length} sources`}
              </button>
            )}
          </div>
        )}
        {sourceUrl && (
          <a
            href={sourceUrl}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              display: "block",
              fontSize: 10.5,
              color: ACCENT,
              textDecoration: "underline",
              wordBreak: "break-all",
              marginBottom: 12,
            }}
          >
            {sourceUrl}
          </a>
        )}

        {/* Evidence drill-down — for strain satellites with a lineage
            relation to the subject: the raw observations behind THIS edge. */}
        {isLineageSatellite && (
          <EvidenceExpander node={node} response={response} />
        )}

        {/* CTA */}
        {onOpenReport && (
          <button
            onClick={onOpenReport}
            style={{
              width: "100%",
              padding: "10px 12px",
              background: ACCENT,
              color: BG_DEEP,
              border: "none",
              borderRadius: 8,
              fontFamily: "Inter, sans-serif",
              fontSize: 10,
              letterSpacing: "0.12em",
              textTransform: "uppercase",
              fontWeight: 600,
              cursor: "pointer",
            }}
          >
            View full dossier →
          </button>
        )}
      </div>
    </div>
  );
}

function MetaCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-[var(--border)] px-2.5 py-1.5" style={{ background: "rgba(31,58,47,0.6)" }}>
      <div className="mb-0.5 text-[7.5px] uppercase tracking-[0.14em]" style={{ color: TEXT_FAINT }}>
        {label}
      </div>
      <div className="text-[11.5px] capitalize" style={{ color: TEXT_PRIMARY }}>{value}</div>
    </div>
  );
}

// =============================================================================
// Evidence expander — the raw observation rows behind ONE lineage edge.
// Quarantine semantics are respected implicitly: the backend already
// excludes quarantined observations from this payload.
// =============================================================================

/** Lineage relations a satellite strain can hold to the subject. */
const LINEAGE_RELATIONS = new Set([
  "parent",
  "child",
  "sibling",
  "ancestor",
  "descendant",
]);

/** The (child, parent) pair whose observations this card should show.
 * There is only ever a direct edge for parent/child; for sibling and
 * ancestor/descendant we show the first hop of the connecting path and
 * label it honestly. */
function resolveEvidenceEdge(
  response: NeighborhoodResponse,
  selectedSlug: string,
  relation: string
): { childSlug: string; parentSlug: string } | null {
  const subject = response.slug;
  const parentsOf = (slug: string) =>
    response.edges
      .filter((e) => e.type === "CHILD_OF" && e.source === slug)
      .map((e) => String(e.target));
  const childrenOf = (slug: string) =>
    response.edges
      .filter((e) => e.type === "CHILD_OF" && e.target === slug)
      .map((e) => String(e.source));
  const upstream = (slug: string): Set<string> => {
    const seen = new Set<string>();
    const stack = [...parentsOf(slug)];
    while (stack.length) {
      const s = stack.pop() as string;
      if (seen.has(s)) continue;
      seen.add(s);
      stack.push(...parentsOf(s));
    }
    return seen;
  };
  const downstream = (slug: string): Set<string> => {
    const seen = new Set<string>();
    const stack = [...childrenOf(slug)];
    while (stack.length) {
      const s = stack.pop() as string;
      if (seen.has(s)) continue;
      seen.add(s);
      stack.push(...childrenOf(s));
    }
    return seen;
  };

  switch (relation) {
    case "parent":
      return { childSlug: subject, parentSlug: selectedSlug };
    case "child":
      return { childSlug: selectedSlug, parentSlug: subject };
    case "sibling": {
      const shared = parentsOf(subject).find((p) => parentsOf(selectedSlug).includes(p));
      return shared ? { childSlug: subject, parentSlug: shared } : null;
    }
    case "ancestor": {
      const up = upstream(selectedSlug);
      const hop = parentsOf(subject).find((p) => up.has(p));
      return hop ? { childSlug: subject, parentSlug: hop } : null;
    }
    case "descendant": {
      const down = downstream(selectedSlug);
      const hop = childrenOf(subject).find((c) => down.has(c));
      return hop ? { childSlug: hop, parentSlug: subject } : null;
    }
    default:
      return null;
  }
}

type EvidenceEntry =
  | { status: "loading" }
  | { status: "error"; error: string }
  | { status: "done"; data: EvidenceResponse | null };

function EvidenceExpander({
  node,
  response,
}: {
  node: NeighborhoodNode;
  response: NeighborhoodResponse;
}) {
  const rel = (node.data.relation as string) || "related";
  const selectedSlug = String(node.data.slug ?? node.id);
  const target = resolveEvidenceEdge(response, selectedSlug, rel);

  // Evidence cache per child slug, in component state — expanding a second
  // node reuses what already came over the wire.
  const [cache, setCache] = useState<Record<string, EvidenceEntry>>({});
  const [openFor, setOpenFor] = useState<string | null>(null);
  const open = openFor === selectedSlug && target !== null;
  const entry = target ? cache[target.childSlug] : undefined;

  const handleToggle = () => {
    if (open || !target) {
      setOpenFor(open ? null : selectedSlug);
      return;
    }
    setOpenFor(selectedSlug);
    if (!cache[target.childSlug]) {
      setCache((prev) => ({ ...prev, [target.childSlug]: { status: "loading" } }));
      fetchEdgeEvidence(target.childSlug)
        .then((data) =>
          setCache((prev) => ({
            ...prev,
            [target.childSlug]: { status: "done", data },
          }))
        )
        .catch((e: unknown) =>
          setCache((prev) => ({
            ...prev,
            [target.childSlug]: {
              status: "error",
              error: e instanceof Error ? e.message : "Could not load evidence",
            },
          }))
        );
    }
  };

  const edge =
    entry?.status === "done" && entry.data && target
      ? entry.data.edges.find((e) => e.parent === target.parentSlug) ?? null
      : null;
  const observations = edge?.observations ?? [];
  const domains = new Set(observations.map((o) => sourceDomain(o.sourceUrl)));

  return (
    <div className="mb-4 rounded-xl border border-[var(--border)] bg-[var(--bg-surface)]/40">
      <button
        type="button"
        onClick={handleToggle}
        disabled={!target}
        aria-expanded={open}
        aria-controls="evidence-expander"
        className="flex w-full cursor-pointer items-center justify-between gap-2 rounded-xl px-3 py-2 text-left disabled:cursor-default"
        style={{ background: "none", border: "none" }}
      >
        <span className="text-[9px] uppercase tracking-[0.14em]" style={{ color: TEXT_FAINT }}>
          Evidence
        </span>
        <span className="text-[10px]" style={{ color: TEXT_MUTED }}>
          {open ? "▾" : "▸"}
        </span>
      </button>

      {open && target && (
        <div id="evidence-expander" className="px-3 pb-3">
          <div className="mb-2 text-[9.5px]" style={{ color: TEXT_MUTED }}>
            Observations behind{" "}
            <span style={{ color: TEXT_PRIMARY }}>
              {target.childSlug} → {target.parentSlug}
            </span>
          </div>

          {entry?.status === "loading" && (
            <span className="flex items-center gap-2 text-[11px]" style={{ color: TEXT_MUTED }}>
              <span
                className="h-3 w-3 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent"
                aria-hidden
              />
              Loading evidence…
            </span>
          )}

          {entry?.status === "error" && (
            <div className="text-[11px]" style={{ color: getTrustTierColor("CONTRADICTED") }}>
              {entry.error}
            </div>
          )}

          {entry?.status === "done" && (
            <>
              {observations.length > 0 && (
                <div className="mb-2 text-[9px] uppercase tracking-[0.12em]" style={{ color: TEXT_FAINT }}>
                  {observations.length} observation{observations.length === 1 ? "" : "s"} from{" "}
                  {domains.size} domain{domains.size === 1 ? "" : "s"}
                </div>
              )}
              {observations.length === 0 ? (
                <div className="text-[11px] leading-relaxed" style={{ color: TEXT_MUTED }}>
                  {edge
                    ? "No observations recorded on this edge — the connection is asserted nowhere in the current record."
                    : `No evidence rows connect ${target.childSlug} → ${target.parentSlug} yet. A missing connection is more honest than a false one.`}
                </div>
              ) : (
                <div className="flex flex-col gap-2.5">
                  {observations.map((o, i) => (
                    <div
                      key={`${o.sourceUrl}-${o.observedAt}-${i}`}
                      className="border-t pt-2 first:border-t-0 first:pt-0"
                      style={{ borderColor: BORDER }}
                    >
                      <div
                        className="mb-1 text-[11px] italic leading-relaxed"
                        style={{ color: TEXT_MUTED }}
                      >
                        {o.excerpt ? `“${o.excerpt}”` : "No excerpt recorded"}
                      </div>
                      <div className="text-[10px]" style={{ color: TEXT_PRIMARY }}>
                        {o.sourceTitle || sourceDomain(o.sourceUrl) || "Untitled source"}
                      </div>
                      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[9.5px]" style={{ color: TEXT_FAINT }}>
                        <span>
                          {o.engine || "unknown engine"} · {relativeTime(o.observedAt)}
                        </span>
                        <span className="tabular-nums">
                          {Math.round((o.confidence ?? 0) * 100)}% confidence
                        </span>
                      </div>
                      {o.sourceUrl && (
                        <div className="mt-1 flex flex-wrap items-center gap-x-3">
                          <a
                            href={o.sourceUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="break-all underline underline-offset-2"
                            style={{ color: ACCENT, fontSize: 9.5 }}
                          >
                            {o.sourceUrl}
                          </a>
                          {o.waybackUrl && (
                            <a
                              href={o.waybackUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              title="Archived copy captured by the Wayback Machine"
                              className="underline underline-offset-2"
                              style={{ color: TEXT_MUTED, fontSize: 9.5 }}
                            >
                              archived copy ↗
                            </a>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
