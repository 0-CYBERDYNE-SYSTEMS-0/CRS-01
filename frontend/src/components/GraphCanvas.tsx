"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  TRUST_TIER_META,
  getTrustTierColor,
  getTrustTierLabel,
  nodeKindLabel,
  type TrustTier,
  type NeighborhoodResponse,
  type NeighborhoodNode,
} from "@/lib/types";

// =============================================================================
// Visual constants — Earth Palette tokens. Tier hexes come from
// TRUST_TIER_META (the single token source) — never redeclared here.
// =============================================================================

const TIER_VERIFIED = TRUST_TIER_META.VERIFIED.color;
const TIER_COMMUNITY = TRUST_TIER_META.COMMUNITY_CONSENSUS.color;
const TIER_ANECDOTAL = TRUST_TIER_META.ANECDOTAL.color;
const TIER_CONTRADICTED = TRUST_TIER_META.CONTRADICTED.color;
const BG_DEEP = "#0F2A1F";
const BG_SURFACE = "#1F3A2F";
const BORDER = "#2A4A3A";
const TEXT_PRIMARY = "#EDE6D8";
const TEXT_MUTED = "#8B9A8E";
const TEXT_FAINT = "#5A6B5F";
const ACCENT = TIER_VERIFIED;

// =============================================================================
// Polar geometry — 460×460 wheel with subject at center.
//
// Sector spans are disjoint by construction (there is a ≥9° moat between
// neighbors) so parents can never land in the claim arc or vice versa.
// Angles are SVG radians: 0 = right, +π/2 = bottom, π = left, 3π/2 = top.
// =============================================================================

const WHEEL_W = 460;
const WHEEL_H = 460;
const WHEEL_CX = WHEEL_W / 2;
const WHEEL_CY = WHEEL_H / 2;
const WHEEL_R_MIN = 78;
const WHEEL_R_MAX = 196;

const SECTOR_SPANS: Record<string, [number, number]> = {
  parent:  [-Math.PI * 0.45,  Math.PI * 0.08], // top → upper-right
  sibling: [ Math.PI * 0.13,  Math.PI * 0.47], // right
  breeder: [ Math.PI * 0.52,  Math.PI * 0.88], // bottom
  claim:   [ Math.PI * 0.93,  Math.PI * 1.48], // left, sweeping up toward top
};

type SectorId = keyof typeof SECTOR_SPANS;

export interface PolarPlacement {
  x: number;
  y: number;
  angle: number;
  sectorId: SectorId;
}

function nodeBox(n: NeighborhoodNode): { w: number; h: number } {
  if (n.type === "Claim") return { w: 132, h: 50 };
  if (n.type === "Person") return { w: 100, h: 36 };
  return { w: 112, h: 36 };
}

// ── Sector placement by real relation OR fallback heuristic ──────────
function bucketNodes(
  nodes: NeighborhoodNode[],
  response: NeighborhoodResponse,
  showDisagreements: boolean
): Record<SectorId, NeighborhoodNode[]> {
  const centerId = response.center_node_id;
  const disagreements = new Set<string>();
  for (const e of response.edges) {
    if (e.data?.agreement === "disagreement") {
      disagreements.add(e.source);
      disagreements.add(e.target);
    }
  }

  // Mark for exclusion: nodes ONLY reachable through disagreement edges.
  const excluded = new Set<string>();
  if (!showDisagreements) {
    const nodeAgreements = new Map<string, string[]>();
    for (const e of response.edges) {
      const ag = e.data?.agreement || "single_source";
      for (const nid of [e.source, e.target]) {
        if (!nodeAgreements.has(nid)) nodeAgreements.set(nid, []);
        nodeAgreements.get(nid)!.push(ag);
      }
    }
    for (const [nid, ags] of nodeAgreements) {
      if (nid === centerId) continue;
      if (ags.every((a) => a === "disagreement")) {
        excluded.add(nid);
      }
    }
  }

  const buckets: Record<SectorId, NeighborhoodNode[]> = {
    parent: [], sibling: [], breeder: [], claim: [],
  };

  for (const n of nodes) {
    if (n.id === centerId || excluded.has(n.id)) continue;
    const rel = (n.data.relation as string) || "";

    if (n.type === "Claim" || rel === "claim") {
      buckets.claim.push(n);
    } else if (n.type === "Person" || rel === "breeder") {
      buckets.breeder.push(n);
    } else if (["parent", "ancestor"].includes(rel)) {
      buckets.parent.push(n);
    } else if (["sibling", "child", "descendant", "related"].includes(rel)) {
      buckets.sibling.push(n);
    } else {
      // Fallback heuristic for nodes without relation (legacy data).
      const type = n.type as string;
      if (type === "Strain") {
        const siblingHints = ["amnesia", "mkultra", "haze", "kush"];
        const isSibling = siblingHints.some((h) => n.id.toLowerCase().includes(h));
        buckets[isSibling ? "sibling" : "parent"].push(n);
      } else if (type === "Person") {
        buckets.breeder.push(n);
      } else {
        buckets.claim.push(n);
      }
    }
  }

  return buckets;
}

// ── Deterministic overlap relaxation ─────────────────────────────────
// Polar placement alone lets sibling cards collide when confidences (and
// therefore radii) are close. Push overlapping boxes apart along their
// shallowest axis, keep everyone inside the wheel, off the center disc,
// and (weakly) inside their own sector.
function relaxPlacements(
  placements: Map<string, PolarPlacement>,
  nodesById: Map<string, NeighborhoodNode>
) {
  const ids = Array.from(placements.keys());
  if (ids.length < 2) return;

  const pos = ids.map((id) => ({ ...placements.get(id)! }));
  const orig = ids.map((id) => ({ ...placements.get(id)! }));
  const half = ids.map((id) => {
    const b = nodeBox(nodesById.get(id)!);
    return { w: b.w / 2 + 4, h: b.h / 2 + 4 };
  });

  const ITER = 80;
  for (let it = 0; it < ITER; it++) {
    let moved = false;
    for (let i = 0; i < pos.length; i++) {
      for (let j = i + 1; j < pos.length; j++) {
        const a = pos[i], b = pos[j];
        const dx = b.x - a.x, dy = b.y - a.y;
        const ox = half[i].w + half[j].w - Math.abs(dx);
        const oy = half[i].h + half[j].h - Math.abs(dy);
        if (ox > 0 && oy > 0) {
          moved = true;
          if (ox < oy * 0.8) {
            const push = (dx >= 0 ? 1 : -1) * (ox / 2 + 0.5);
            a.x -= push; b.x += push;
          } else {
            const push = (dy >= 0 ? 1 : -1) * (oy / 2 + 0.5);
            a.y -= push; b.y += push;
          }
        }
      }
    }
    for (let i = 0; i < pos.length; i++) {
      const p = pos[i], o = orig[i];
      // Weak spring back toward the sector placement so relaxation
      // resolves collisions without letting nodes wander between arcs.
      p.x += (o.x - p.x) * 0.03;
      p.y += (o.y - p.y) * 0.03;
      // Keep inside the wheel frame…
      p.x = Math.min(Math.max(p.x, half[i].w + 4), WHEEL_W - half[i].w - 4);
      p.y = Math.min(Math.max(p.y, half[i].h + 6), WHEEL_H - half[i].h - 8);
      // …and off the center disc.
      const ddx = p.x - WHEEL_CX, ddy = p.y - WHEEL_CY;
      const dist = Math.hypot(ddx, ddy);
      const minDist = 64;
      if (dist < minDist && dist > 0.001) {
        p.x = WHEEL_CX + (ddx / dist) * minDist;
        p.y = WHEEL_CY + (ddy / dist) * minDist;
      }
    }
    if (!moved) break;
  }

  ids.forEach((id, i) => {
    const p = placements.get(id)!;
    p.x = pos[i].x;
    p.y = pos[i].y;
  });
}

// ── Wheel capacity ───────────────────────────────────────────────────
// The claims arc physically fits ~6 cards; beyond that the wheel turns
// into the pile-up this layout exists to fix. Show the top claims by
// confidence and point at the dossier/sources for the full list.
export const WHEEL_CLAIM_LIMIT = 6;

// ── Public placement helper (wheel + report preview share it) ────────
export function computeWheelData(
  response: NeighborhoodResponse,
  showDisagreements: boolean
): { placements: Map<string, PolarPlacement>; hiddenClaims: number } {
  const placements = new Map<string, PolarPlacement>();
  const buckets = bucketNodes(response.nodes, response, showDisagreements);

  // Cap the claims bucket: top confidence first, id as a deterministic
  // tiebreaker.
  const ranked = [...buckets.claim].sort(
    (a, b) =>
      (b.data.confidence ?? 0.5) - (a.data.confidence ?? 0.5) ||
      a.id.localeCompare(b.id)
  );
  const hiddenClaims = Math.max(0, ranked.length - WHEEL_CLAIM_LIMIT);
  buckets.claim = ranked.slice(0, WHEEL_CLAIM_LIMIT);

  (Object.keys(buckets) as SectorId[]).forEach((sid) => {
    const items = buckets[sid];
    const [a0, a1] = SECTOR_SPANS[sid];
    items.forEach((n, i) => {
      const angle = a0 + (a1 - a0) * (items.length <= 1 ? 0.5 : (i + 0.5) / items.length);
      const conf = (n.data.confidence ?? 0.5);
      // Three staggered radius lanes break the "same ring" pile-up before
      // relaxation runs; ±14 keeps radial ≈ confidence legible.
      const lane = ((i % 3) - 1) * 14;
      const dist = WHEEL_R_MIN + conf * (WHEEL_R_MAX - WHEEL_R_MIN) + lane;
      placements.set(n.id, {
        x: WHEEL_CX + Math.cos(angle) * dist,
        y: WHEEL_CY + Math.sin(angle) * dist,
        angle,
        sectorId: sid,
      });
    });
  });

  const nodesById = new Map(response.nodes.map((n) => [n.id, n]));
  relaxPlacements(placements, nodesById);
  return { placements, hiddenClaims };
}

// =============================================================================
// Trust-tier palette — getTrustTierColor is the only tier→hex path.
// =============================================================================

function edgeAgreementColor(edgeData: { color?: string } | undefined): string {
  const c = edgeData?.color;
  if (c === "green") return TIER_COMMUNITY;
  if (c === "red") return TIER_CONTRADICTED;
  return TIER_ANECDOTAL;
}

// =============================================================================
// Polar Wheel SVG — interactive: hover popovers, relationship highlighting,
// click-to-select (the detail card renders in the page's context rail).
//
// Render hierarchy (bottom → top):
//   1. confidence rings + sector arcs (backdrop)
//   2. spokes (connectors, emphasized for the hovered node)
//   3. nodes (content; unrelated ones dim when something is focused)
//   4. hover popover (top-most, never obscured)
// =============================================================================

function PolarWheel({
  response,
  placements,
  hoveredId,
  selectedId,
  onHover,
  onSelect,
}: {
  response: NeighborhoodResponse;
  placements: Map<string, PolarPlacement>;
  hoveredId: string | null;
  selectedId: string | null;
  onHover: (id: string | null) => void;
  onSelect: (id: string | null) => void;
}) {
  const centerNode = response.nodes.find((n) => n.id === response.center_node_id);
  const centerLabel = centerNode?.data.name ?? centerNode?.label ?? response.slug;
  // Missing tier falls back to ANECDOTAL — never assert an unearned tier.
  const centerTier: TrustTier = (centerNode?.data.trust_tier as TrustTier) ?? "ANECDOTAL";
  const centerConf = centerNode?.data.confidence ?? 0.5;
  const centerColor = getTrustTierColor(centerTier);
  const centerOrigin = (centerNode?.data.origin as string) || "";
  const centerImage = (centerNode?.data.image_url as string) || "";
  const hasPortrait = centerImage !== "";

  // ── Relationship layers: direct neighbors of the hovered node stay
  //    bright; everything else recedes. Hover wins; when the pointer is
  //    elsewhere, an open selection keeps itself + its neighbors bright. ─
  const focusId = hoveredId ?? selectedId;
  const neighbors = useMemo(() => {
    const set = new Set<string>();
    if (focusId) {
      set.add(focusId);
      for (const e of response.edges) {
        if (e.source === focusId) set.add(e.target);
        if (e.target === focusId) set.add(e.source);
      }
    }
    return set;
  }, [focusId, response.edges]);

  const hoveredNode = hoveredId
    ? response.nodes.find((n) => n.id === hoveredId)
    : null;
  // The center node has no placements entry — synthesize one so its
  // hover popover renders like any satellite's.
  let hoveredPlacement: PolarPlacement | null =
    (hoveredId ? placements.get(hoveredId) : null) ?? null;
  if (!hoveredPlacement && hoveredId === response.center_node_id) {
    hoveredPlacement = { x: WHEEL_CX, y: WHEEL_CY, angle: -Math.PI / 2, sectorId: "parent" };
  }
  const activeDim = hoveredId !== null || selectedId !== null;

  const SECTOR_LABELS: Record<SectorId, { label: string; color: string }> = {
    parent:  { label: "PARENTS",   color: TIER_VERIFIED },
    sibling: { label: "RELATED",   color: TIER_COMMUNITY },
    breeder: { label: "BREEDERS",  color: TIER_ANECDOTAL },
    claim:   { label: "CLAIMS",    color: TIER_VERIFIED },
  };

  return (
    <>
      {/* ── Layer 0: click-empty-space to deselect ─────────────── */}
      <rect
        x={0} y={0} width={WHEEL_W} height={WHEEL_H}
        fill="transparent"
        onClick={() => onSelect(null)}
      />

      {/* ── Layer 1: confidence rings ─────────────────────────── */}
      {[0.4, 0.6, 0.8, 1.0].map((v) => {
        const r = WHEEL_R_MIN + v * (WHEEL_R_MAX - WHEEL_R_MIN);
        return (
          <g key={v}>
            <circle cx={WHEEL_CX} cy={WHEEL_CY} r={r} fill="none" stroke={BORDER} strokeOpacity={0.45} strokeDasharray="1 4" />
            <text x={WHEEL_CX + 4} y={WHEEL_CY - r + 4} fill={TEXT_FAINT} fontFamily="Inter, sans-serif" fontSize={8.5} letterSpacing="0.08em">
              {Math.round(v * 100)}%
            </text>
          </g>
        );
      })}

      {/* ── Layer 1: sector arcs ──────────────────────────────── */}
      {(Object.keys(SECTOR_SPANS) as SectorId[]).map((sid) => {
        const [a0, a1] = SECTOR_SPANS[sid];
        const r1 = WHEEL_R_MIN, r2 = WHEEL_R_MAX + 14;
        const x0 = WHEEL_CX + Math.cos(a0) * r1, y0 = WHEEL_CY + Math.sin(a0) * r1;
        const x1 = WHEEL_CX + Math.cos(a1) * r1, y1 = WHEEL_CY + Math.sin(a1) * r1;
        const x2 = WHEEL_CX + Math.cos(a1) * r2, y2 = WHEEL_CY + Math.sin(a1) * r2;
        const x3 = WHEEL_CX + Math.cos(a0) * r2, y3 = WHEEL_CY + Math.sin(a0) * r2;
        const large = a1 - a0 > Math.PI ? 1 : 0;
        const color = SECTOR_LABELS[sid].color;
        const am = (a0 + a1) / 2;
        const lr = WHEEL_R_MAX + 30;
        const lx = Math.min(Math.max(WHEEL_CX + Math.cos(am) * lr, 36), WHEEL_W - 36);
        const ly = Math.min(Math.max(WHEEL_CY + Math.sin(am) * lr, 14), WHEEL_H - 14);
        return (
          <g key={sid}>
            <path
              d={`M${x0},${y0} L${x3},${y3} A${r2},${r2} 0 ${large} 1 ${x2},${y2} L${x1},${y1} A${r1},${r1} 0 ${large} 0 ${x0},${y0} Z`}
              fill={color} fillOpacity={0.05}
              stroke={color} strokeOpacity={0.28} strokeDasharray="2 5"
            />
            <text x={lx} y={ly} textAnchor="middle" fill={color} fontFamily="Inter, sans-serif" fontSize={9.5} letterSpacing="0.16em" fontWeight={500}>
              {SECTOR_LABELS[sid].label}
            </text>
          </g>
        );
      })}

      {/* ── Layer 2: spokes (emphasized for hovered/selected) ─── */}
      {Array.from(placements.entries()).map(([id, p]) => {
        const node = response.nodes.find((n) => n.id === id);
        if (!node) return null;
        const edges = response.edges.filter((e) => e.source === id || e.target === id);
        const cls = edgeAgreementColor(edges[0]?.data);
        const isHot = hoveredId === id || neighbors.has(id);
        return (
          <line
            key={`spoke-${id}`}
            x1={WHEEL_CX} y1={WHEEL_CY} x2={p.x} y2={p.y}
            stroke={cls}
            strokeWidth={isHot ? 2.2 : 1.4}
            strokeOpacity={activeDim && !isHot ? 0.15 : 0.9}
            strokeLinecap="round"
          />
        );
      })}

      {/* ── Layer 3: nodes ─────────────────────────────────────── */}
      {Array.from(placements.entries()).map(([id, p]) => {
        const node = response.nodes.find((n) => n.id === id);
        if (!node) return null;
        const tier: TrustTier | undefined = node.data.trust_tier;
        const color = getTrustTierColor(tier);
        const isPerson = node.type === "Person";
        const isClaim = node.type === "Claim";
        const isHot = hoveredId === id || neighbors.has(id);
        const dimmed = activeDim && !isHot;
        const { w, h } = nodeBox(node);
        const x = p.x - w / 2;
        const y = p.y - h / 2;
        const label = String(node.data.name ?? node.label ?? id);
        const pct = Math.round((node.data.confidence ?? 0.5) * 100);
        const img = (node.data.image_url as string) || "";
        return (
          <g
            key={id}
            transform={`translate(${x},${y})`}
            opacity={dimmed ? 0.3 : 1}
            style={{ cursor: "pointer", transition: "opacity .15s ease" }}
            onMouseEnter={() => onHover(id)}
            onMouseLeave={() => onHover(null)}
            onClick={(e) => { e.stopPropagation(); onSelect(selectedId === id ? null : id); }}
          >
            <rect width={w} height={h} rx={isPerson ? 18 : 8} fill={BG_SURFACE} stroke={color} strokeWidth={isHot ? 1.8 : 1.2} />
            {img && (
              <image
                href={img}
                x={3} y={3}
                width={h - 6} height={h - 6}
                rx={isPerson ? 15 : 4}
                preserveAspectRatio="xMidYMid slice"
                clipPath={`url(#clip-${id})`}
              />
            )}
            <text x={img ? h : w / 2} y={13} textAnchor={img ? "start" : "middle"} fontFamily="Inter, sans-serif" fontSize={7.5} letterSpacing="0.12em" fill={TEXT_MUTED} fontWeight={500}>
              {isClaim ? `CLAIM · ${pct}%` : isPerson ? String(node.data.platform ?? "PERSON").toUpperCase() : "STRAIN"}
            </text>
            {isClaim ? (
              <foreignObject x={6} y={18} width={w - 12} height={h - 22}>
                <div style={{ fontFamily: "var(--font-eb-garamond), 'EB Garamond', serif", fontSize: 10.5, lineHeight: 1.2, color: TEXT_PRIMARY, textAlign: "center", fontStyle: "italic" }}>
                  {label.length > 32 ? label.slice(0, 30) + "…" : label}
                </div>
              </foreignObject>
            ) : (
              <text x={img ? h + 4 : w / 2} y={h - 8} textAnchor={img ? "start" : "middle"} fontFamily="var(--font-eb-garamond), 'EB Garamond', serif" fontSize={13} fontStyle="italic" fill={TEXT_PRIMARY}>
                {isPerson ? "@" + label : label}
              </text>
            )}
          </g>
        );
      })}

      {/* ── Center node (always above spokes) ─────────────────── */}
      <g
        transform={`translate(${WHEEL_CX - 64}, ${WHEEL_CY - 46})`}
        style={{ cursor: "pointer" }}
        onMouseEnter={() => onHover(response.center_node_id)}
        onMouseLeave={() => onHover(null)}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(selectedId === response.center_node_id ? null : response.center_node_id);
        }}
      >
        <circle cx={64} cy={46} r={46} fill={BG_SURFACE} stroke={centerColor} strokeWidth={1.5} />
        <circle cx={64} cy={46} r={46} fill={`${centerColor}20`} pointerEvents="none" />
        {hasPortrait && (
          <>
            <clipPath id="clip-center">
              <circle cx={64} cy={22} r={16} />
            </clipPath>
            <image
              href={centerImage}
              x={48} y={6}
              width={32} height={32}
              preserveAspectRatio="xMidYMid slice"
              clipPath="url(#clip-center)"
            />
            <circle cx={64} cy={22} r={16} fill="none" stroke={centerColor} strokeWidth={0.8} strokeOpacity={0.7} pointerEvents="none" />
          </>
        )}
        <text x={64} y={(hasPortrait ? 50 : 26)} textAnchor="middle" fontFamily="Inter, sans-serif" fontSize={7.5} letterSpacing="0.14em" fill={centerColor} fontWeight={500}>
          SUBJECT
        </text>
        <text x={64} y={(hasPortrait ? 67 : 45)} textAnchor="middle" fontFamily="var(--font-eb-garamond), 'EB Garamond', serif" fontSize={15} fontStyle="italic" fill={TEXT_PRIMARY}>
          {centerLabel}
        </text>
        <text x={64} y={(hasPortrait ? 78 : 58)} textAnchor="middle" fontFamily="Inter, sans-serif" fontSize={7.5} letterSpacing="0.04em" fill={TEXT_MUTED}>
          {TRUST_TIER_META[centerTier]?.badge ?? getTrustTierLabel(centerTier)} · {Math.round(centerConf * 100)}%
        </text>
        {centerOrigin && (
          <text x={64} y={(hasPortrait ? 88 : 69)} textAnchor="middle" fontFamily="Inter, sans-serif" fontSize={6.5} letterSpacing="0.1em" fill={ACCENT}>
            {centerOrigin.toUpperCase()}
          </text>
        )}
      </g>

      {/* ── Layer 5: hover popover — always top-most ─────────── */}
      {hoveredId && hoveredNode && hoveredPlacement && selectedId !== hoveredId && (
        <HoverPopover
          node={hoveredNode}
          placement={hoveredPlacement}
          edgeCount={response.edges.filter(
            (e) => e.source === hoveredId || e.target === hoveredId
          ).length}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Hover popover — small provenance card that rides above the wheel
// ---------------------------------------------------------------------------

const POPOVER_W = 176;

function HoverPopover({
  node,
  placement,
  edgeCount,
}: {
  node: NeighborhoodNode;
  placement: PolarPlacement;
  edgeCount: number;
}) {
  const tier: TrustTier | undefined = node.data.trust_tier;
  const color = getTrustTierColor(tier);
  const name = String(node.data.name ?? node.data.handle ?? node.label ?? node.id);
  const kind = nodeKindLabel(node);
  const rel = (node.data.relation as string) || "related";
  const conf = Math.round((node.data.confidence ?? 0.5) * 100);

  // Place above-left of the node; clamp inside the wheel viewBox.
  let px = placement.x + 14;
  let py = placement.y - 118;
  if (px + POPOVER_W > WHEEL_W - 8) px = placement.x - POPOVER_W - 14;
  if (py < 8) py = placement.y + 26;

  return (
    <foreignObject
      x={px} y={py} width={POPOVER_W} height={110}
      pointerEvents="none"
      style={{ overflow: "visible" }}
    >
      <div
        style={{
          background: "rgba(19,26,21,0.97)",
          border: `1px solid ${color}`,
          borderRadius: 10,
          padding: "10px 12px",
          fontFamily: "Inter, sans-serif",
          color: TEXT_PRIMARY,
          boxShadow: "0 8px 32px rgba(0,0,0,0.65)",
          backdropFilter: "blur(6px)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 5 }}>
          <span style={{ fontSize: 8, letterSpacing: "0.14em", color: TEXT_MUTED, textTransform: "uppercase", fontWeight: 600 }}>
            {kind}
          </span>
          <span style={{ fontSize: 8, letterSpacing: "0.08em", color: TEXT_FAINT, textTransform: "uppercase" }}>
            {rel}
          </span>
          {tier && (
            <span style={{ marginLeft: "auto", fontSize: 7.5, letterSpacing: "0.06em", color, border: `1px solid ${color}60`, borderRadius: 4, padding: "1px 5px", textTransform: "uppercase" }}>
              {getTrustTierLabel(tier)}
            </span>
          )}
        </div>
        <div style={{ fontFamily: "var(--font-eb-garamond), 'EB Garamond', serif", fontSize: 15, fontStyle: "italic", color: TEXT_PRIMARY, marginBottom: 6, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
          {name}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <div style={{ flex: 1, height: 3, background: BORDER, borderRadius: 2, overflow: "hidden" }}>
            <div style={{ width: `${conf}%`, height: "100%", background: color, borderRadius: 2 }} />
          </div>
          <span style={{ fontSize: 9, color: TEXT_MUTED, fontVariantNumeric: "tabular-nums" }}>
            {conf}%
          </span>
        </div>
        <div style={{ marginTop: 6, fontSize: 9, letterSpacing: "0.06em", color: TEXT_FAINT, textTransform: "uppercase" }}>
          {edgeCount} connection{edgeCount === 1 ? "" : "s"} · click for detail
        </div>
      </div>
    </foreignObject>
  );
}

// =============================================================================
// Main GraphCanvas — the graph pane of the workspace.
// The wheel always fits on load; zoom in with the controls or mouse wheel,
// drag to pan, double-click (or ⛶) to return to fit.
// =============================================================================

const MIN_ZOOM = 1;
const MAX_ZOOM = 3;

export interface GraphCanvasProps {
  response: NeighborhoodResponse;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  /** Transient status text (e.g. "Refreshing…"). */
  status?: string | null;
}

export default function GraphCanvas({
  response,
  selectedId,
  onSelect,
  status = null,
}: GraphCanvasProps) {
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const [showDisagreements, setShowDisagreements] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 }); // viewBox units
  const svgRef = useRef<SVGSVGElement | null>(null);
  const dragState = useRef<{ px: number; py: number; panX: number; panY: number } | null>(null);
  const dragMoved = useRef(false);

  const { placements, hiddenClaims } = useMemo(
    () => computeWheelData(response, showDisagreements),
    [response, showDisagreements]
  );

  const disagreementCount = response.edges.filter(
    (e) => e.data?.agreement === "disagreement"
  ).length;

  // ── Zoom / pan helpers ─────────────────────────────────────────────
  const clampPan = useCallback((p: { x: number; y: number }, z: number) => {
    const lim = (WHEEL_W / 2) * (z - 1);
    return {
      x: Math.min(Math.max(p.x, -lim), lim),
      y: Math.min(Math.max(p.y, -lim), lim),
    };
  }, []);

  const applyZoom = useCallback(
    (nextRaw: number) => {
      const z = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, nextRaw));
      setZoom(z);
      setPan((prev) => (z <= MIN_ZOOM ? { x: 0, y: 0 } : clampPan(prev, z)));
    },
    [clampPan]
  );

  const resetView = useCallback(() => {
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, []);

  // Reset whenever the strain changes.
  useEffect(() => {
    resetView();
  }, [response.slug, resetView]);

  // Wheel zoom needs a non-passive native listener to preventDefault.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      applyZoom(zoom * Math.exp(-e.deltaY * 0.0014));
    };
    svg.addEventListener("wheel", onWheel, { passive: false });
    return () => svg.removeEventListener("wheel", onWheel);
  }, [zoom, applyZoom]);

  const pxToVb = () => {
    const svg = svgRef.current;
    if (!svg) return 1;
    const r = svg.getBoundingClientRect();
    return WHEEL_W / Math.max(1, Math.min(r.width, r.height));
  };

  const handlePointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (zoom <= MIN_ZOOM) return;
    dragState.current = { px: e.clientX, py: e.clientY, panX: pan.x, panY: pan.y };
    dragMoved.current = false;
    svgRef.current?.setPointerCapture(e.pointerId);
  };

  const handlePointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const d = dragState.current;
    if (!d) return;
    const k = pxToVb();
    const dx = (e.clientX - d.px) * k;
    const dy = (e.clientY - d.py) * k;
    if (Math.hypot(e.clientX - d.px, e.clientY - d.py) > 4) dragMoved.current = true;
    setPan(clampPan({ x: d.panX + dx, y: d.panY + dy }, zoom));
  };

  const handlePointerUp = () => {
    dragState.current = null;
    // Let the click event fire first, then clear the moved flag.
    setTimeout(() => { dragMoved.current = false; }, 0);
  };

  // Swallow node clicks that were really the end of a pan drag.
  const guardedSelect = useCallback(
    (id: string | null) => {
      if (dragMoved.current) return;
      onSelect(id);
    },
    [onSelect]
  );

  const transform = `translate(${(1 - zoom) * WHEEL_CX + pan.x} ${(1 - zoom) * WHEEL_CY + pan.y}) scale(${zoom})`;

  return (
    <div
      className="pane-vignette relative h-full w-full overflow-hidden"
      style={{
        background: BG_DEEP,
        backgroundImage:
          "radial-gradient(circle at 30% 25%, rgba(212,160,23,0.07), transparent 60%), radial-gradient(circle at 70% 80%, rgba(45,212,191,0.06), transparent 65%)",
      }}
    >
      <svg
        ref={svgRef}
        viewBox={`0 0 ${WHEEL_W} ${WHEEL_H}`}
        preserveAspectRatio="xMidYMid meet"
        style={{
          width: "100%",
          height: "100%",
          display: "block",
          cursor: dragState.current ? "grabbing" : zoom > MIN_ZOOM ? "grab" : "default",
          touchAction: "none",
        }}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
        onDoubleClick={resetView}
      >
        <g transform={transform}>
          <PolarWheel
            response={response}
            placements={placements}
            hoveredId={hoveredId}
            selectedId={selectedId}
            onHover={setHoveredId}
            onSelect={guardedSelect}
          />
        </g>
      </svg>

      {/* ── Top-left chrome: disputes toggle + status ──────────── */}
      <div className="pointer-events-none absolute left-3.5 top-3.5 z-10 flex flex-col items-start gap-1.5">
        {disagreementCount > 0 && (
          <button
            type="button"
            onClick={() => setShowDisagreements((v) => !v)}
            className="pointer-events-auto flex cursor-pointer items-center gap-1.5 rounded-full border px-3 py-1.5 text-[10px] uppercase tracking-[0.1em] transition-colors"
            style={{
              borderColor: showDisagreements ? TIER_CONTRADICTED : BORDER,
              color: showDisagreements ? TIER_CONTRADICTED : TEXT_MUTED,
              background: showDisagreements ? "rgba(239,68,68,0.12)" : "rgba(19,26,21,0.72)",
              backdropFilter: "blur(10px)",
            }}
          >
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{ background: showDisagreements ? TIER_CONTRADICTED : TEXT_FAINT }}
              aria-hidden
            />
            {disagreementCount} disput{disagreementCount === 1 ? "e" : "es"}
          </button>
        )}
        {status && (
          <div
            className="pointer-events-auto rounded-full border px-3 py-1 text-[10px] uppercase tracking-[0.08em]"
            style={{
              borderColor: BORDER,
              color: TEXT_MUTED,
              background: "rgba(19,26,21,0.9)",
            }}
          >
            {status}
          </div>
        )}
      </div>

      {/* ── Top-right chrome: zoom controls ────────────────────── */}
      <div className="absolute right-3.5 top-3.5 z-10 flex flex-col gap-1.5">
        {[
          { label: "Zoom in", glyph: "+", action: () => applyZoom(zoom * 1.4), disabled: zoom >= MAX_ZOOM },
          { label: "Zoom out", glyph: "−", action: () => applyZoom(zoom / 1.4), disabled: zoom <= MIN_ZOOM },
          { label: "Fit to view", glyph: "⛶", action: resetView, disabled: zoom <= MIN_ZOOM },
        ].map((b) => (
          <button
            key={b.label}
            type="button"
            title={b.label}
            aria-label={b.label}
            disabled={b.disabled}
            onClick={b.action}
            className="flex h-[30px] w-[30px] cursor-pointer items-center justify-center rounded-lg border text-[14px] leading-none transition-colors disabled:cursor-default disabled:opacity-35"
            style={{
              borderColor: BORDER,
              color: TEXT_MUTED,
              background: "rgba(19,26,21,0.72)",
              backdropFilter: "blur(10px)",
            }}
            onMouseEnter={(e) => { if (!b.disabled) { e.currentTarget.style.color = TEXT_PRIMARY; e.currentTarget.style.borderColor = ACCENT; } }}
            onMouseLeave={(e) => { e.currentTarget.style.color = TEXT_MUTED; e.currentTarget.style.borderColor = BORDER; }}
          >
            {b.glyph}
          </button>
        ))}
      </div>

      {/* ── Bottom chrome: legend + readout ────────────────────── */}
      <div
        className="absolute inset-x-4 bottom-3.5 z-10 flex flex-wrap items-center justify-between gap-3 text-[9.5px] uppercase tracking-[0.1em]"
        style={{ color: TEXT_MUTED }}
      >
        <span className="flex flex-wrap items-center gap-4">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: TIER_COMMUNITY }} />
            Multi-source
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full" style={{ background: TIER_ANECDOTAL }} />
            Single source
          </span>
          {disagreementCount > 0 && (
            <span className="flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-full" style={{ background: TIER_CONTRADICTED }} />
              Disagreed
            </span>
          )}
        </span>
        <span className="flex items-center gap-4">
          {hiddenClaims > 0 && (
            <span
              className="normal-case italic tracking-normal"
              style={{ color: TIER_VERIFIED, fontFamily: "var(--font-eb-garamond), 'EB Garamond', serif", fontSize: 11 }}
            >
              +{hiddenClaims} more claim{hiddenClaims === 1 ? "" : "s"} in the dossier
            </span>
          )}
          <span
            className="normal-case italic tracking-normal"
            style={{ color: TEXT_FAINT, fontFamily: "var(--font-eb-garamond), 'EB Garamond', serif", fontSize: 11 }}
          >
            radial = confidence
          </span>
          <span style={{ fontVariantNumeric: "tabular-nums" }}>
            depth {response.depth} · {response.stats.node_count} nodes
          </span>
        </span>
      </div>
    </div>
  );
}
