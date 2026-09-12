"use client";

import { useState } from "react";

import { submitResearch, submitStrainReview } from "@/lib/api-client";
import { formatDate, relativeTime } from "@/lib/time";
import {
  CURATABLE_TIERS,
  TRUST_TIER_META,
  getTrustTierColor,
  getTrustTierLabel,
  sourceDomain,
  type TrustTier,
  type NeighborhoodResponse,
  type NeighborhoodNode,
  type LineageClaim,
} from "@/lib/types";
import { computeWheelData } from "@/components/GraphCanvas";

// =============================================================================
// ReportView — the research dossier as a full-width editorial page.
// Cream paper on a darker desk, EB Garamond at readable sizes, an
// evidence-grounded lead (verbatim, attributed), a reader-facing stat band,
// lineage statements grouped into distinct accounts (with conflicts shown as
// conflicts), and claim-by-claim provenance with source links.
// =============================================================================

// Desk + paper palette (intentional print theming — no tier colors in here;
// the gold accent IS the verified gold, sourced from the one token table).
const DESK = "#E7DFCC";
const PAPER = "#F6F2E8";
const PAPER_EDGE = "#C8C0AC";
const INK = "#1F2A22";
const INK_SOFT = "#2A3A2E";
const INK_MUTED = "#5A6B5F";
const INK_FAINT = "#8B9A8E";
const GOLD_DIM = "#8B6914";
const TEAL = "#1F7872";
const ACCENT = TRUST_TIER_META.VERIFIED.color;

const SERIF = "var(--font-eb-garamond), 'EB Garamond', Georgia, serif";
const SANS = "var(--font-inter), Inter, sans-serif";

// ── Mini wheel preview (non-interactive; click returns to the graph) ──
function MiniWheel({
  response,
  onClick,
}: {
  response: NeighborhoodResponse;
  onClick: () => void;
}) {
  const placements = computeWheelData(response, false).placements;
  const centerNode = response.nodes.find((n) => n.id === response.center_node_id);
  const centerLabel = centerNode?.data.name ?? centerNode?.label ?? response.slug;
  const centerColor = getTrustTierColor(centerNode?.data.trust_tier as TrustTier);

  return (
    <button
      type="button"
      onClick={onClick}
      title="Open the interactive graph"
      className="group block w-full cursor-pointer rounded-xl border p-3 text-left transition-shadow hover:shadow-lg"
      style={{ borderColor: PAPER_EDGE, background: "#FBF8F0" }}
    >
      <svg viewBox="0 0 460 460" style={{ width: "100%", display: "block" }}>
        {[0.4, 0.6, 0.8, 1.0].map((v) => (
          <circle
            key={v}
            cx={230} cy={230}
            r={78 + v * (196 - 78)}
            fill="none"
            stroke={PAPER_EDGE}
            strokeOpacity={0.6}
            strokeDasharray="1 4"
          />
        ))}
        {Array.from(placements.entries()).map(([id, p]) => {
          const node = response.nodes.find((n) => n.id === id);
          if (!node) return null;
          const color = getTrustTierColor(node.data.trust_tier);
          return (
            <g key={id}>
              <line x1={230} y1={230} x2={p.x} y2={p.y} stroke={color} strokeWidth={1.4} strokeOpacity={0.55} />
              <rect
                x={p.x - 8} y={p.y - 5}
                width={16} height={10} rx={3}
                fill="#F6F2E8" stroke={color} strokeWidth={1.4}
              />
            </g>
          );
        })}
        <circle cx={230} cy={230} r={40} fill={PAPER} stroke={centerColor} strokeWidth={1.6} />
        <circle cx={230} cy={230} r={40} fill={`${centerColor}1A`} />
        <text x={230} y={224} textAnchor="middle" fontFamily={SANS} fontSize={9} letterSpacing="0.16em" fill={GOLD_DIM}>
          SUBJECT
        </text>
        <text x={230} y={244} textAnchor="middle" fontFamily={SERIF} fontSize={17} fontStyle="italic" fill={INK}>
          {centerLabel.length > 14 ? centerLabel.slice(0, 13) + "…" : centerLabel}
        </text>
      </svg>
      <div
        className="mt-1 text-center text-[9px] uppercase tracking-[0.18em] transition-colors group-hover:text-[#1F2A22]"
        style={{ color: INK_FAINT, fontFamily: SANS }}
      >
        Open interactive graph →
      </div>
    </button>
  );
}

function TierChip({ tier }: { tier?: TrustTier }) {
  if (!tier) return null;
  const color = getTrustTierColor(tier);
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[9px] font-medium uppercase tracking-[0.08em]"
      style={{ color, borderColor: `${color}88`, fontFamily: SANS }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} aria-hidden />
      {getTrustTierLabel(tier)}
    </span>
  );
}

/** Gold provenance badge — this tier was set by a human curator, and the
 * note (if any) is the attribution. Never rendered for machine verdicts. */
function CuratedBadge({ note, tier }: { note?: string | null; tier?: TrustTier | null }) {
  const tooltip = note
    ? `Curated by a human: ${note}`
    : "Trust tier set by human curation";
  return (
    <span
      title={tooltip}
      className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[9px] font-medium uppercase tracking-[0.08em]"
      style={{ color: ACCENT, borderColor: `${ACCENT}88`, fontFamily: SANS }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: ACCENT }} aria-hidden />
      Curated{tier ? ` · ${getTrustTierLabel(tier)}` : ""}
    </span>
  );
}

/** The human tier-override control. Honest by construction: whatever a
 * curator picks is stamped with origin='curated' server-side, so the badge
 * above always discloses it was a human decision. */
function CurateControl({
  slug,
  currentTier,
  onDataChanged,
}: {
  slug: string;
  currentTier: TrustTier;
  onDataChanged?: () => void;
}) {
  const [tier, setTier] = useState<string>("");
  const [note, setNote] = useState("");
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applied, setApplied] = useState(false);

  const handleApply = async () => {
    setApplying(true);
    setError(null);
    setApplied(false);
    try {
      await submitStrainReview(
        slug,
        tier === "" ? null : (tier as TrustTier),
        note.trim()
      );
      setApplied(true);
      setNote("");
      onDataChanged?.();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Review failed");
    } finally {
      setApplying(false);
    }
  };

  return (
    <details className="relative" style={{ fontFamily: SANS }}>
      <summary
        className="cursor-pointer select-none rounded-lg border px-3 py-1.5 text-[9.5px] uppercase tracking-[0.14em] transition-colors hover:bg-[#FBF8F0]"
        style={{ borderColor: PAPER_EDGE, color: INK_MUTED }}
      >
        Curate
      </summary>
      <div
        className="absolute left-0 top-full z-20 mt-1.5 w-[260px] rounded-xl border p-3 shadow-lg"
        style={{ background: "#FBF8F0", borderColor: PAPER_EDGE }}
      >
        <div className="mb-1.5 text-[8.5px] uppercase tracking-[0.14em]" style={{ color: INK_FAINT }}>
          Human review of {slug}
        </div>
        <select
          value={tier}
          onChange={(e) => {
            setTier(e.target.value);
            setApplied(false);
          }}
          className="mb-2 w-full cursor-pointer rounded-md border bg-white px-2 py-1.5 text-[11px]"
          style={{ borderColor: PAPER_EDGE, color: INK }}
        >
          <option value="">Clear review — re-derive from evidence</option>
          {CURATABLE_TIERS.map((t) => (
            <option key={t} value={t}>
              {getTrustTierLabel(t)}
              {t === "VERIFIED" ? " (human-verified)" : ""}
            </option>
          ))}
        </select>
        <textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          maxLength={2000}
          rows={2}
          placeholder="Why? Shown as the curation provenance."
          className="mb-2 w-full resize-none rounded-md border px-2 py-1.5 text-[11px]"
          style={{ borderColor: PAPER_EDGE, color: INK, background: "white" }}
        />
        <button
          type="button"
          onClick={handleApply}
          disabled={applying || (tier !== "" && tier === currentTier && !note.trim())}
          className="w-full cursor-pointer rounded-md px-2 py-1.5 text-[9.5px] font-semibold uppercase tracking-[0.12em] transition-opacity disabled:cursor-not-allowed disabled:opacity-50"
          style={{ background: ACCENT, color: "#0F2A1F", border: "none" }}
        >
          {applying ? "Applying…" : tier === "" ? "Clear review" : "Apply review"}
        </button>
        {error && (
          <div className="mt-1.5 text-[10px]" style={{ color: "#B3261E" }}>
            {error}
          </div>
        )}
        {applied && !error && (
          <div className="mt-1.5 text-[10px]" style={{ color: TEAL }}>
            Review applied — the dossier reflects it.
          </div>
        )}
        <div className="mt-1.5 text-[9px] leading-relaxed" style={{ color: INK_FAINT }}>
          VERIFIED exists only here — it is never assigned automatically.
          Contradicted is data-derived and cannot be hand-picked.
        </div>
      </div>
    </details>
  );
}

// =============================================================================
// Lineage parsing & display helpers.
// ALL grouping here is display-layer only: the API payload keeps every claim
// row (raw evidence is append-only), and nothing is deduplicated at the data
// layer — near-identical assertions are collapsed visually into one account.
// =============================================================================

const LEADING_CONNECTOR_RE = /^(?:as|of|from|by|and|the|a|an)\s+/i;
const TRAILING_JUNK_RE = /\s+(?:genetics|phenotypes?|strain|strains|cross|crosses|breeding|lineage)\.?$/i;

/** Strip leading connector words ("as OG Kush") and trailing junk words
 * ("OG Kush genetics") so sloppy claim values group with their clean twins. */
function cleanParentToken(token: string): string {
  let s = token.trim();
  for (;;) {
    const next = s.replace(LEADING_CONNECTOR_RE, "").replace(TRAILING_JUNK_RE, "");
    if (next === s) break;
    s = next;
  }
  return s.trim();
}

/** Case/space/hyphen-insensitive key for matching names. */
function normKey(s: string): string {
  return s.trim().toLowerCase().replace(/[\s_]+/g, "-").replace(/-+/g, "-");
}

/** Parse "child = parent × parent" (the LINEAGE claim value shape). Values
 * too malformed to split return null and fall through to the
 * "Other recorded assertions" list — demoted, never hidden. */
function parseLineageValue(value: unknown): { child: string; parents: string[] } | null {
  const raw = String(value ?? "").trim();
  const eq = raw.indexOf("=");
  if (eq < 0) return null;
  const child = cleanParentToken(raw.slice(0, eq));
  const parents = raw
    .slice(eq + 1)
    .split("×")
    .map(cleanParentToken)
    .filter((p) => p.length > 0);
  if (parents.length === 0) return null;
  return { child, parents };
}

export interface LineageGroup {
  key: string;
  child: string;
  parents: string[];
  /** Cleaned display form: "child = A × B". */
  assertion: string;
  claims: NeighborhoodNode[];
  /** Distinct non-empty source URLs across the group's claims. */
  sourceUrls: string[];
  /** Representative claim: longest excerpt, then highest confidence. */
  best: NeighborhoodNode;
}

function groupKey(child: string, parents: string[]): string {
  // UNORDERED tuple — "A × B" and "B × A" are one assertion.
  return `${normKey(child)}::${parents.map(normKey).sort().join("§")}`;
}

function groupLineageClaims(claims: NeighborhoodNode[]): {
  groups: LineageGroup[];
  malformed: NeighborhoodNode[];
} {
  const byKey = new Map<string, LineageGroup>();
  const malformed: NeighborhoodNode[] = [];
  for (const c of claims) {
    const parsed = parseLineageValue(c.data.value ?? c.label);
    if (!parsed) {
      malformed.push(c);
      continue;
    }
    const key = groupKey(parsed.child, parsed.parents);
    const existing = byKey.get(key);
    if (existing) {
      existing.claims.push(c);
    } else {
      byKey.set(key, {
        key,
        child: parsed.child,
        parents: parsed.parents,
        assertion: `${parsed.child ? `${parsed.child} = ` : ""}${parsed.parents.join(" × ")}`,
        claims: [c],
        sourceUrls: [],
        best: c,
      });
    }
  }
  const groups = Array.from(byKey.values());
  for (const g of groups) {
    const seen = new Set<string>();
    g.sourceUrls = g.claims
      .map((c) => String((c.data.source_url as string) || ""))
      .filter((u) => {
        if (!u || seen.has(u)) return false;
        seen.add(u);
        return true;
      });
    g.best = [...g.claims].sort((a, b) => {
      const ea = String((a.data.excerpt as string) || "").length;
      const eb = String((b.data.excerpt as string) || "").length;
      if (eb !== ea) return eb - ea;
      const ca = a.data.confidence ?? 0;
      const cb = b.data.confidence ?? 0;
      if (cb !== ca) return cb - ca;
      return String(a.id).localeCompare(String(b.id));
    })[0];
  }
  return { groups, malformed };
}

/** Word-boundary excerpt trim with a typographic ellipsis where cut —
 * never mid-word, never rewritten. */
function trimExcerpt(text: unknown, max = 220): string {
  const t = String(text ?? "").replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const cut = t.slice(0, max);
  const sp = cut.lastIndexOf(" ");
  return `${(sp > 0 ? cut.slice(0, sp) : cut).replace(/[,;:.\s]+$/, "")}…`;
}

function isHttpSource(url: string): boolean {
  return /^https?:\/\//i.test(url);
}

/** True when a parent token is a generic phrase rather than a strain name.
 * Strain names arrive capitalized from extraction (even landraces like
 * "Brazilian Sativa"); an all-lowercase multi-word token is a description
 * that leaked through ("together plants", "relevant characteristics").
 * Display-level only — such groups are demoted, never deleted. */
function isGenericParentPhrase(parent: string): boolean {
  const words = parent.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return false;
  return words.every((w) => !/^[A-Z0-9]/.test(w));
}

/** Label for provider pseudo-URLs (tavily://answer/…) — rendered as plain
 * muted text, never as a clickable link. Null for real http(s) URLs. */
function providerLabel(url: string): string | null {
  const m = url.match(/^([a-z][a-z0-9+.-]*):/i);
  if (!m) return null;
  const scheme = m[1].toLowerCase();
  if (scheme === "http" || scheme === "https") return null;
  return `Provider answer · ${scheme.charAt(0).toUpperCase()}${scheme.slice(1)}`;
}

// ── Provenance cards — one per distinct assertion group ──────────────

interface ProvenanceCard {
  key: string;
  type: string;
  sourceCount: number;
  assertion: string;
  excerpt: string;
  tier?: TrustTier;
  confidence: number;
  sourceUrls: string[];
  titles: Record<string, string>;
  waybackUrl: string;
}

function sourceTitles(claims: NeighborhoodNode[]): Record<string, string> {
  const titles: Record<string, string> = {};
  for (const c of claims) {
    const u = String((c.data.source_url as string) || "");
    const t = String((c.data.source_title as string) || "").trim();
    if (u && t && !titles[u]) titles[u] = t;
  }
  return titles;
}

function firstWayback(claims: NeighborhoodNode[]): string {
  for (const c of claims) {
    const w = String((c.data.wayback_url as string) || "");
    if (w) return w;
  }
  return "";
}

function ProvenanceCardView({ card, index }: { card: ProvenanceCard; index: number }) {
  const httpSources = card.sourceUrls.filter(isHttpSource);
  const providerSources = card.sourceUrls.filter((u) => !isHttpSource(u));
  const shown = httpSources.slice(0, 3);
  const rest = httpSources.slice(3);

  const renderSourceLink = (u: string) => {
    const title = card.titles[u];
    return (
      <a
        key={u}
        href={u}
        target="_blank"
        rel="noopener noreferrer"
        className="text-[11.5px] underline underline-offset-2 hover:opacity-80"
        style={{ color: GOLD_DIM, fontFamily: SANS }}
      >
        {title ? `${sourceDomain(u)} — ${trimExcerpt(title, 60)}` : sourceDomain(u)}
      </a>
    );
  };

  return (
    <article
      className="flex flex-col rounded-xl border p-5"
      style={{ borderColor: PAPER_EDGE, background: "#FBF8F0" }}
    >
      <div className="mb-2.5 flex items-center justify-between gap-3">
        <span className="text-[9px] uppercase tracking-[0.14em]" style={{ color: INK_FAINT, fontFamily: SANS }}>
          {card.type}
          {card.sourceCount > 1 ? ` · ${card.sourceCount} sources` : ""} · № {index + 1}
        </span>
        <span className="flex items-center gap-2">
          <TierChip tier={card.tier} />
          <span className="text-[10.5px] tabular-nums" style={{ color: INK_MUTED, fontFamily: SANS }}>
            {Math.round(card.confidence * 100)}%
          </span>
        </span>
      </div>
      <div className="mb-3" style={{ fontFamily: SERIF, fontSize: 18, lineHeight: 1.35, color: INK }}>
        {card.assertion}
      </div>
      {card.excerpt && (
        <blockquote
          className="mb-3 border-l-2 pl-3 text-[12.5px] italic leading-relaxed"
          style={{ borderColor: PAPER_EDGE, color: INK_MUTED, fontFamily: SERIF }}
        >
          “{card.excerpt}”
        </blockquote>
      )}
      {card.sourceUrls.length === 0 ? (
        <span className="mt-auto text-[11px]" style={{ color: INK_FAINT, fontFamily: SANS }}>
          No source URL recorded
        </span>
      ) : (
        <div className="mt-auto flex flex-col gap-1">
          {shown.map(renderSourceLink)}
          {rest.length > 0 && (
            <details>
              <summary
                className="cursor-pointer text-[10.5px] hover:opacity-80"
                style={{ color: INK_FAINT, fontFamily: SANS }}
              >
                +{rest.length} more source{rest.length === 1 ? "" : "s"}
              </summary>
              <div className="mt-1.5 flex flex-col gap-1">{rest.map(renderSourceLink)}</div>
            </details>
          )}
          {providerSources.map((u) => (
            <span key={u} className="text-[10.5px]" style={{ color: INK_FAINT, fontFamily: SANS }}>
              {providerLabel(u) ?? u}
            </span>
          ))}
          {card.waybackUrl && (
            <a
              href={card.waybackUrl}
              target="_blank"
              rel="noopener noreferrer"
              title="Archived copy captured by the Wayback Machine"
              className="break-all text-[10px] underline underline-offset-2 hover:opacity-80"
              style={{ color: INK_FAINT, fontFamily: SANS }}
            >
              Archived copy ↗
            </a>
          )}
        </div>
      )}
    </article>
  );
}

// =============================================================================
// The dossier
// =============================================================================

export default function ReportView({
  response,
  onBack,
  onDataChanged,
}: {
  response: NeighborhoodResponse;
  onBack: () => void;
  onDataChanged?: () => void;
}) {
  const [deepenClaims, setDeepenClaims] = useState<LineageClaim[] | null>(null);
  const [deepening, setDeepening] = useState(false);

  const center = response.nodes.find((n) => n.id === response.center_node_id);
  const name = center?.data.name ?? center?.label ?? response.slug;
  const imageUrl = (center?.data.image_url as string) || "";
  const summary = (center?.data.summary as string) || "";
  const summarySourceUrl = String(
    (center?.data.summary_source_url as string | null | undefined) ?? ""
  ).trim();
  const summarySourceTitle = String(
    (center?.data.summary_source_title as string | null | undefined) ?? ""
  ).trim();
  const hasAttributedSummary = Boolean(summary && summarySourceUrl && isHttpSource(summarySourceUrl));

  // Profile line — only what the record actually contains.
  const propsData = (center?.data.props ?? {}) as Record<string, unknown>;
  const profileLine = ["type", "thc_range", "breeder"]
    .map((k) => (typeof propsData[k] === "string" ? (propsData[k] as string).trim() : ""))
    .filter(Boolean);

  const verified = response.stats.trust_distribution["VERIFIED"] ?? 0;
  const community = response.stats.trust_distribution["COMMUNITY_CONSENSUS"] ?? 0;
  const anecdotal = response.stats.trust_distribution["ANECDOTAL"] ?? 0;
  const contradicted = response.stats.trust_distribution["CONTRADICTED"] ?? 0;
  const subjectPct = Math.round((center?.data.confidence ?? 0) * 100);
  // Missing tier falls back to ANECDOTAL — never assert an unearned tier.
  const subjectTier = (center?.data.trust_tier as TrustTier) ?? "ANECDOTAL";
  const origin = ((center?.data.origin as string) || "researched").toUpperCase();
  const curatedNote = center?.data.curated_note as string | null | undefined;
  const curatedTier = center?.data.curated_tier as TrustTier | null | undefined;
  const isCurated = origin === "CURATED";

  const claimNodesRaw = response.nodes.filter((n) => n.type === "Claim");
  const engines = new Set<string>();
  let greenEdges = 0, amberEdges = 0, redEdges = 0;
  for (const e of response.edges) {
    const ag = (e.data as Record<string, unknown>)?.agreement || "single_source";
    if (ag === "multi_source") greenEdges++;
    else if (ag === "disagreement") redEdges++;
    else amberEdges++;
    const domains = (e.data as Record<string, unknown>)?.source_domains;
    if (Array.isArray(domains)) {
      for (const d of domains) engines.add(String(d));
    }
  }
  // Claim edges on a shallow neighborhood carry no source_domains, yet the
  // claims themselves cite URLs — count those hostnames too, so the dossier
  // never claims "0 domains" while quoting one.
  for (const c of claimNodesRaw) {
    const u = (c.data as Record<string, unknown>).source_url as string | undefined;
    if (u && /^https?:\/\//i.test(u)) {
      try {
        engines.add(new URL(u).hostname.replace(/^www\./, ""));
      } catch {
        /* unparseable URL — not a domain we claim */
      }
    }
  }
  const totalEdges = greenEdges + amberEdges + redEdges;
  // Reader-meaningful sources count: the backend's DISTINCT claim source
  // URLs; falls back to the edge-domain count when the field is absent.
  const sourcesConsulted = response.stats.sources_consulted ?? engines.size;

  const claimNodes = claimNodesRaw;
  const lineageClaims = claimNodes.filter((c) => (c.data.type as string) === "LINEAGE");
  const otherClaims = claimNodes.filter((c) => (c.data.type as string) !== "LINEAGE");

  // Group near-duplicate lineage assertions into distinct parent-set
  // accounts (display-layer only — the claims table is untouched).
  const { groups: allParsedGroups, malformed: malformedParsed } = groupLineageClaims(lineageClaims);
  // Display-level demotion: a group asserting a generic phrase where a
  // strain name should be (e.g. "together plants × relevant characteristics",
  // scraped from a crop-science article) stays on record but is not
  // presented as a parentage account.
  const junkGroups = allParsedGroups.filter((g) => g.parents.some(isGenericParentPhrase));
  const malformedLineage: NeighborhoodNode[] = [...malformedParsed];
  for (const g of junkGroups) {
    if (!malformedLineage.some((c) => c.id === g.best.id)) malformedLineage.push(g.best);
  }
  const parsedGroups = allParsedGroups.filter((g) => !junkGroups.includes(g));
  const subjectKeys = new Set([normKey(response.slug), normKey(name)]);
  const lineageGroups = [...parsedGroups].sort((a, b) => {
    const sa = subjectKeys.has(normKey(a.child)) ? 0 : 1;
    const sb = subjectKeys.has(normKey(b.child)) ? 0 : 1;
    if (sa !== sb) return sa - sb;
    if (b.claims.length !== a.claims.length) return b.claims.length - a.claims.length;
    return a.assertion.localeCompare(b.assertion);
  });
  const subjectGroups = lineageGroups
    .filter((g) => subjectKeys.has(normKey(g.child)))
    .sort((a, b) =>
      b.sourceUrls.length !== a.sourceUrls.length
        ? b.sourceUrls.length - a.sourceUrls.length
        : a.assertion.localeCompare(b.assertion)
    );
  // Subject rows speak the canonical display name, however the claim's
  // child field was cased when extracted ("gmo" → "GMO").
  for (const g of subjectGroups) g.assertion = `${name} = ${g.parents.join(" × ")}`;
  // 2+ distinct parent sets asserted for the subject = conflicting accounts.
  const conflictingAccounts = subjectGroups.length >= 2;
  // Other strains' assertions stay on record but out of the subject's way —
  // the dossier is the subject's; neighbors collapse into one disclosure.
  const relatedGroups = lineageGroups.filter((g) => !subjectKeys.has(normKey(g.child)));

  // Provenance cards: one card per distinct subject lineage account (with
  // its quoted excerpt) + one per other claim type. Related strains' lineage
  // stays on record in the collapsed "Related strains" list above.
  const provenanceCards: ProvenanceCard[] = [
    ...subjectGroups.map((g) => ({
      key: g.key,
      type: "LINEAGE",
      sourceCount: g.sourceUrls.length,
      assertion: g.assertion,
      excerpt: trimExcerpt(g.best.data.excerpt),
      tier: (g.best.data.trust_tier as TrustTier) ?? undefined,
      confidence: typeof g.best.data.confidence === "number" ? g.best.data.confidence : 0.5,
      sourceUrls: g.sourceUrls,
      titles: sourceTitles(g.claims),
      waybackUrl: firstWayback(g.claims),
    })),
    ...otherClaims.map((c) => {
      const u = String((c.data.source_url as string) || "");
      return {
        key: c.id,
        type: String((c.data.type as string) || "CLAIM"),
        sourceCount: u ? 1 : 0,
        assertion: String(c.data.value ?? c.label),
        excerpt: trimExcerpt(c.data.excerpt),
        tier: (c.data.trust_tier as TrustTier) ?? undefined,
        confidence: typeof c.data.confidence === "number" ? c.data.confidence : 0.5,
        sourceUrls: u ? [u] : [],
        titles: sourceTitles([c]),
        waybackUrl: firstWayback([c]),
      };
    }),
  ];

  // Reader stat band — every number traceable to API fields.
  const statBand: Array<{ value: number | string; label: string; color: string }> = [
    { value: subjectGroups.length, label: "Parent sets recorded", color: GOLD_DIM },
    ...(conflictingAccounts
      ? [{ value: subjectGroups.length, label: "Conflicting accounts", color: getTrustTierColor("CONTRADICTED") }]
      : []),
    { value: sourcesConsulted, label: "Sources consulted", color: TEAL },
    { value: engines.size, label: "Independent domains", color: INK_SOFT },
    { value: `${subjectPct}%`, label: "Subject confidence", color: INK },
    // Verified is human-only and stays unmentioned until it exists.
    ...(verified > 0 ? [{ value: verified, label: "Verified", color: ACCENT }] : []),
  ];

  // Freshness — from the KB's own timestamps. Absent timestamps mean the
  // segment (or the whole line) is omitted, never rendered as "Unknown".
  const lastResearched = (center?.data.last_researched as number | null | undefined) ?? null;
  const firstSeen = formatDate(center?.data.first_seen as number | null | undefined);
  const freshness: string[] = [];
  if (lastResearched) freshness.push(`Last researched ${relativeTime(lastResearched)}`);
  if (firstSeen) freshness.push(`First seen ${firstSeen}`);

  const navEntries: Array<{ href: string; label: string }> = [
    { href: "#record", label: "The record — confidence, agreement, and what they mean" },
    ...(lineageClaims.length > 0
      ? [{ href: "#lineage", label: `Lineage on file — parentage accounts for ${name}` }]
      : []),
    ...(conflictingAccounts
      ? [{ href: "#conflicts", label: "Conflicting accounts — where sources disagree" }]
      : []),
    ...(provenanceCards.length > 0
      ? [{ href: "#provenance", label: "Claim provenance — each claim with its public source" }]
      : []),
  ];
  const numerals = ["i", "ii", "iii", "iv", "v"];

  // Deepen = a real research run for the subject. It is a network call in
  // dev by design: the run fans out across providers, lands in the ledger,
  // and its lineage claims speak the one shared claim shape.
  const handleDeepen = async () => {
    setDeepening(true);
    try {
      const r = await submitResearch(name, 2, 30);
      setDeepenClaims(r.run.lineage_claims);
    } catch {
      setDeepenClaims([]);
    } finally {
      setDeepening(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto" style={{ background: DESK }}>
      <div className="mx-auto max-w-[1120px] px-8 pb-20 pt-6 xl:px-12">
        {/* ── Toolbar row ─────────────────────────────────────── */}
        <div className="mb-6 flex items-center justify-between">
          <button
            type="button"
            onClick={onBack}
            className="cursor-pointer rounded-lg border px-3.5 py-1.5 text-[10.5px] uppercase tracking-[0.12em] transition-colors hover:bg-[#F6F2E8]"
            style={{ borderColor: PAPER_EDGE, color: INK_MUTED, fontFamily: SANS, background: "transparent" }}
          >
            ← Back to graph
          </button>
          <span className="text-[10px] uppercase tracking-[0.2em]" style={{ color: INK_FAINT, fontFamily: SANS }}>
            Research Dossier · {origin}
          </span>
        </div>

        {/* ── Paper sheet ─────────────────────────────────────── */}
        <div
          className="rounded-2xl border px-10 pb-14 pt-12 shadow-[0_24px_80px_rgba(31,42,34,0.25)] xl:px-16"
          style={{ background: PAPER, borderColor: PAPER_EDGE, color: INK }}
        >
          {/* Masthead */}
          <div className="grid grid-cols-1 gap-10 lg:grid-cols-[1fr_300px]">
            <div>
              <div
                className="mb-4 text-[10px] uppercase tracking-[0.24em]"
                style={{ color: GOLD_DIM, fontFamily: SANS }}
              >
                CRS-01 · Cannabis Research Sentinel
              </div>
              <h1
                className="mb-4"
                style={{ fontFamily: SERIF, fontSize: "clamp(2.6rem, 5vw, 3.6rem)", lineHeight: 1.02, fontWeight: 500, letterSpacing: "-0.01em", color: INK }}
              >
                {name}
              </h1>
              {profileLine.length > 0 && (
                <div
                  className="mb-4 text-[10.5px] uppercase tracking-[0.2em]"
                  style={{ color: INK_SOFT, fontFamily: SANS }}
                >
                  {profileLine.join(" · ")}
                </div>
              )}
              {summary ? (
                hasAttributedSummary ? (
                  <>
                    <blockquote
                      className="mb-2 max-w-[54ch]"
                      style={{ fontFamily: SERIF, fontStyle: "italic", fontSize: 17.5, lineHeight: 1.55, color: INK_SOFT }}
                    >
                      “{summary}”
                    </blockquote>
                    <div className="mb-1">
                      <a
                        href={summarySourceUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        title={summarySourceTitle || undefined}
                        className="text-[10px] uppercase tracking-[0.16em] underline underline-offset-2 hover:opacity-80"
                        style={{ color: INK_FAINT, fontFamily: SANS }}
                      >
                        — {sourceDomain(summarySourceUrl)}
                      </a>
                    </div>
                  </>
                ) : (
                  <p
                    className="mb-4 max-w-[54ch]"
                    style={{ fontFamily: SERIF, fontStyle: "italic", fontSize: 17, lineHeight: 1.5, color: INK_MUTED }}
                  >
                    {summary}
                  </p>
                )
              ) : (
                <p
                  className="mb-4 max-w-[54ch]"
                  style={{ fontFamily: SERIF, fontStyle: "italic", fontSize: 17, lineHeight: 1.5, color: INK_MUTED }}
                >
                  {`${response.stats.node_count} entities observed across ${response.stats.node_types["Strain"] ?? 0} strains at depth ${response.depth}.`}
                </p>
              )}
              <div
                className="text-[10px] uppercase tracking-[0.18em]"
                style={{
                  color: INK_FAINT,
                  fontFamily: SANS,
                  marginBottom: freshness.length > 0 ? 10 : 28,
                }}
              >
                {engines.size > 0
                  ? `Via ${Array.from(engines).slice(0, 4).join(" · ")}${engines.size > 4 ? ` +${engines.size - 4} more` : ""}`
                  : `Depth ${response.depth} · ${response.stats.node_count} nodes`}
              </div>
              {freshness.length > 0 && (
                <div
                  className="mb-7 text-[10px] uppercase tracking-[0.18em] tabular-nums"
                  style={{ color: INK_FAINT, fontFamily: SANS }}
                >
                  {freshness.join(" · ")}
                </div>
              )}
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  onClick={handleDeepen}
                  disabled={deepening}
                  className="cursor-pointer rounded-lg px-4 py-2.5 text-[10.5px] font-semibold uppercase tracking-[0.14em] transition-opacity disabled:opacity-60"
                  style={{ background: ACCENT, color: "#0F2A1F", border: "none", fontFamily: SANS }}
                >
                  {deepening ? "Searching the open web…" : "Deepen research"}
                </button>
                <TierChip tier={subjectTier} />
                {isCurated && (
                  <CuratedBadge note={curatedNote} tier={curatedTier} />
                )}
                <span className="text-[11px] tabular-nums" style={{ color: INK_MUTED, fontFamily: SANS }}>
                  subject confidence {subjectPct}%
                </span>
                <CurateControl
                  slug={response.slug}
                  currentTier={subjectTier}
                  onDataChanged={onDataChanged}
                />
              </div>

              {/* Contents — mirrors the real section list below */}
              <nav className="mt-10" aria-label="Dossier contents">
                <div className="mb-2.5 text-[9px] uppercase tracking-[0.2em]" style={{ color: INK_FAINT, fontFamily: SANS }}>
                  In this dossier
                </div>
                <ol className="flex flex-col gap-1.5" style={{ fontFamily: SERIF, fontSize: 15.5, color: INK_SOFT }}>
                  {navEntries.map((entry, i) => (
                    <li key={entry.href} className="flex items-baseline gap-3">
                      <span className="tabular-nums" style={{ color: GOLD_DIM, fontSize: 13 }}>
                        {numerals[i]}
                      </span>
                      <a href={entry.href} className="hover:underline underline-offset-4">
                        {entry.label}
                      </a>
                    </li>
                  ))}
                </ol>
              </nav>
            </div>

            <div className="flex flex-col gap-4">
              {imageUrl && (
                <img
                  src={imageUrl}
                  alt={name}
                  referrerPolicy="no-referrer"
                  className="h-[150px] w-full rounded-xl border object-cover"
                  style={{ borderColor: PAPER_EDGE }}
                />
              )}
              <MiniWheel response={response} onClick={onBack} />
            </div>
          </div>

          {/* Stat band — reader stats, not graph jargon */}
          <div
            className="mt-12 flex flex-wrap gap-y-6 border-y py-6"
            style={{ borderColor: PAPER_EDGE }}
          >
            {statBand.map((s, i) => (
              <div
                key={s.label}
                className="min-w-[120px] flex-1 px-5"
                style={{ borderLeft: i > 0 ? `1px solid ${PAPER_EDGE}` : undefined }}
              >
                <div
                  className="tabular-nums"
                  style={{ fontFamily: SERIF, fontSize: 34, lineHeight: 1, color: s.color, fontWeight: 500 }}
                >
                  {s.value}
                </div>
                <div className="mt-2 text-[8.5px] uppercase tracking-[0.16em]" style={{ color: INK_MUTED, fontFamily: SANS }}>
                  {s.label}
                </div>
              </div>
            ))}
          </div>

          {/* ── The record ─────────────────────────────────────── */}
          <section id="record" className="mt-12 max-w-[70ch]">
            <h2 className="mb-4 text-[11px] uppercase tracking-[0.22em]" style={{ color: GOLD_DIM, fontFamily: SANS }}>
              The record
            </h2>
            <div style={{ fontFamily: SERIF, fontSize: 16.5, lineHeight: 1.65, color: INK_SOFT }}>
              <p className="mb-4">
                {name} is anchored at {subjectPct}% confidence ({getTrustTierLabel(subjectTier)} tier).
                The neighborhood spans {response.stats.node_count} nodes at depth {response.depth}.
                {" "}Trust composition: {community} community, {anecdotal} anecdotal
                {contradicted > 0 ? `, ${contradicted} contradicted` : ""}
                {verified > 0 ? `, ${verified} verified` : ""}.
              </p>
              <p className="mb-4">
                {totalEdges > 0 ? (
                  <>
                    Edge agreement: {greenEdges} multi-source ({Math.round((greenEdges / totalEdges) * 100)}%)
                    {amberEdges > 0 ? `, ${amberEdges} single-source` : ""}
                    {redEdges > 0 ? `, ${redEdges} where sources disagree` : ""}.
                  </>
                ) : (
                  <>No lineage edges recorded yet.</>
                )}{" "}
                {sourcesConsulted > 0 && (
                  <>
                    This dossier draws on {sourcesConsulted} consulted source{sourcesConsulted === 1 ? "" : "s"}
                    {engines.size > 0
                      ? ` across ${engines.size} independent domain${engines.size === 1 ? "" : "s"}`
                      : ""}
                    .
                  </>
                )}
                {hasAttributedSummary && (
                  <> The description above is quoted verbatim from {sourceDomain(summarySourceUrl)}.</>
                )}
              </p>
            </div>
          </section>

          {/* ── Lineage ────────────────────────────────────────── */}
          {lineageClaims.length > 0 && (
            <section id="lineage" className="mt-12">
              <h2 className="mb-1 text-[11px] uppercase tracking-[0.22em]" style={{ color: GOLD_DIM, fontFamily: SANS }}>
                Lineage on file
              </h2>
              <p className="mb-5" style={{ color: INK_MUTED, fontFamily: SERIF, fontSize: 14, fontStyle: "italic" }}>
                One line per distinct parentage account on record for {name}, with the number of
                sources asserting it.
              </p>
              <ul className="flex flex-col gap-3">
                {subjectGroups.map((g) => (
                  <li
                    key={g.key}
                    className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b pb-3"
                    style={{ borderColor: PAPER_EDGE }}
                  >
                    <span style={{ fontFamily: SERIF, fontSize: 22, fontStyle: "italic", color: INK }}>
                      {g.assertion}
                    </span>
                    <span className="ml-auto flex items-center gap-3">
                      <span className="text-[11px] tabular-nums" style={{ color: INK_MUTED, fontFamily: SANS }}>
                        {g.sourceUrls.length} source{g.sourceUrls.length === 1 ? "" : "s"}
                      </span>
                      <TierChip tier={g.best.data.trust_tier as TrustTier} />
                      <span className="text-[11px] tabular-nums" style={{ color: INK_MUTED, fontFamily: SANS }}>
                        {Math.round(((g.best.data.confidence as number | undefined) ?? 0.5) * 100)}%
                      </span>
                    </span>
                  </li>
                ))}
              </ul>

              {/* Neighboring strains' assertions — demoted, never hidden. */}
              {relatedGroups.length > 0 && (
                <details className="mt-8">
                  <summary
                    className="cursor-pointer text-[10px] uppercase tracking-[0.16em] hover:opacity-80"
                    style={{ color: INK_FAINT, fontFamily: SANS }}
                  >
                    Related strains on file ({relatedGroups.length})
                  </summary>
                  <ul className="mt-4 flex flex-col gap-2.5">
                    {relatedGroups.map((g) => (
                      <li
                        key={g.key}
                        className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b pb-2.5"
                        style={{ borderColor: PAPER_EDGE }}
                      >
                        <span style={{ fontFamily: SERIF, fontSize: 16, fontStyle: "italic", color: INK_SOFT }}>
                          {g.assertion}
                        </span>
                        <span className="ml-auto flex items-center gap-3">
                          <span className="text-[10.5px] tabular-nums" style={{ color: INK_MUTED, fontFamily: SANS }}>
                            {g.sourceUrls.length} source{g.sourceUrls.length === 1 ? "" : "s"}
                          </span>
                          <TierChip tier={g.best.data.trust_tier as TrustTier} />
                        </span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {/* Conflicting parent sets — the honest conflict story. */}
              {conflictingAccounts && (
                <div id="conflicts" className="mt-10">
                  <h3
                    className="mb-1 text-[10.5px] uppercase tracking-[0.2em]"
                    style={{ color: getTrustTierColor("CONTRADICTED"), fontFamily: SANS }}
                  >
                    Conflicting accounts
                  </h3>
                  <p className="mb-4" style={{ color: INK_MUTED, fontFamily: SERIF, fontSize: 14, fontStyle: "italic" }}>
                    Sources assert different parent sets for {name}. Every account is kept on
                    record — no account is treated as the truth.
                  </p>
                  <div className="flex flex-col gap-3">
                    {subjectGroups.map((g, i) => (
                      <div
                        key={g.key}
                        className="flex flex-wrap items-baseline gap-x-4 gap-y-1 rounded-xl border p-4"
                        style={{ borderColor: PAPER_EDGE, background: "#FBF8F0" }}
                      >
                        <span
                          className="text-[9px] uppercase tracking-[0.14em]"
                          style={{ color: getTrustTierColor("CONTRADICTED"), fontFamily: SANS }}
                        >
                          Account {String.fromCharCode(65 + i)}
                        </span>
                        <span style={{ fontFamily: SERIF, fontSize: 19, fontStyle: "italic", color: INK }}>
                          {g.parents.join(" × ")}
                        </span>
                        <span className="ml-auto flex items-center gap-3">
                          <span className="text-[11px] tabular-nums" style={{ color: INK_MUTED, fontFamily: SANS }}>
                            {g.sourceUrls.length} source{g.sourceUrls.length === 1 ? "" : "s"}
                          </span>
                          <TierChip tier={g.best.data.trust_tier as TrustTier} />
                          <span className="text-[11px] tabular-nums" style={{ color: INK_MUTED, fontFamily: SANS }}>
                            {Math.round(((g.best.data.confidence as number | undefined) ?? 0.5) * 100)}%
                          </span>
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Assertions too malformed to parse — demoted, never hidden. */}
              {malformedLineage.length > 0 && (
                <details className="mt-8">
                  <summary
                    className="cursor-pointer text-[10px] uppercase tracking-[0.16em] hover:opacity-80"
                    style={{ color: INK_FAINT, fontFamily: SANS }}
                  >
                    Other recorded assertions ({malformedLineage.length})
                  </summary>
                  <ul className="mt-4 flex flex-col gap-2.5">
                    {malformedLineage.map((c) => (
                      <li key={c.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                        <span style={{ fontFamily: SERIF, fontSize: 15.5, fontStyle: "italic", color: INK_SOFT }}>
                          {String(c.data.value ?? c.label)}
                        </span>
                        <TierChip tier={c.data.trust_tier as TrustTier} />
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </section>
          )}

          {/* ── Claim provenance ───────────────────────────────── */}
          <section id="provenance" className="mt-12">
            <h2 className="mb-2 text-[11px] uppercase tracking-[0.22em]" style={{ color: GOLD_DIM, fontFamily: SANS }}>
              Claim provenance
            </h2>
            <p className="mb-6" style={{ color: INK_MUTED, fontFamily: SERIF, fontSize: 14, fontStyle: "italic" }}>
              Every assertion below is traceable to a public source. A missing connection is
              more honest than a false one — claims stay listed even when sources disagree.
            </p>
            {provenanceCards.length === 0 ? (
              <p className="text-[14px]" style={{ color: INK_MUTED, fontFamily: SERIF }}>
                No sourced claims recorded for this strain yet.
              </p>
            ) : (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {provenanceCards.map((card, i) => (
                  <ProvenanceCardView key={card.key} card={card} index={i} />
                ))}
              </div>
            )}
          </section>

          {/* ── Colophon ───────────────────────────────────────── */}
          <div
            className="mt-14 border-t pt-5 text-[9.5px] uppercase tracking-[0.16em]"
            style={{ borderColor: PAPER_EDGE, color: INK_FAINT, fontFamily: SANS }}
          >
            Compiled by CRS-01 · {engines.size} {engines.size === 1 ? "domain" : "domains"} · {totalEdges} {totalEdges === 1 ? "edge" : "edges"} · depth {response.depth} ·
            tiers: verified = human curated · community = 2+ domains agree · anecdotal = single source ·
            red = contradicted · graph reading guide: radial distance encodes confidence, edge color marks source agreement
          </div>
        </div>
      </div>

      {/* ── Fresh-from-the-web result card ────────────────────── */}
      {deepenClaims && (
        <div
          className="fixed bottom-6 right-6 z-30 w-[340px] rounded-xl border p-4 shadow-2xl"
          style={{ background: "#1F3A2F", borderColor: "#2A4A3A", color: "#EDE6D8", fontFamily: SANS, fontSize: 11.5 }}
        >
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[9.5px] uppercase tracking-[0.14em]" style={{ color: ACCENT }}>
              {deepenClaims.length > 0
                ? `${deepenClaims.length} new claim${deepenClaims.length === 1 ? "" : "s"} from the web`
                : "No new claims found"}
            </span>
            <button
              onClick={() => setDeepenClaims(null)}
              aria-label="Dismiss"
              className="cursor-pointer rounded border border-[#2A4A3A] px-2 text-[11px] text-[#8B9A8E] hover:text-[#EDE6D8]"
            >
              ✕
            </button>
          </div>
          {deepenClaims.map((c, idx) => (
            <div key={`${c.child}-${c.parent_a}-${c.parent_b}-${idx}`} className="border-t border-[#2A4A3A] py-2.5 first:border-t-0">
              <div className="mb-1 text-[9px] uppercase tracking-[0.1em] text-[var(--trust-anecdotal)]">
                LINEAGE · {Math.round(c.confidence * 100)}% · {c.source_engine}
                {c.tier ? ` · ${getTrustTierLabel(c.tier)}` : ""}
              </div>
              <div className="mb-1 text-[12px] leading-snug">
                {`${c.child || name} = ${c.parent_a} × ${c.parent_b}${c.extra_parents?.length ? " × " + c.extra_parents.join(" × ") : ""}`}
              </div>
              <a
                href={c.source_url}
                target="_blank"
                rel="noreferrer"
                className="text-[9.5px] underline"
                style={{ color: "#8B9A8E" }}
              >
                {c.source_url}
              </a>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
