// =============================================================================
// CRS-01 TypeScript Types — mirrors SPEC.md graph schema
// =============================================================================

// ---------------------------------------------------------------------------
// Trust Tiers
// ---------------------------------------------------------------------------

export type TrustTier =
  | "VERIFIED"
  | "COMMUNITY_CONSENSUS"
  | "ANECDOTAL"
  | "CONTRADICTED";

// ---------------------------------------------------------------------------
// Neighborhood response (GET /api/v1/graph/strains/{slug}/neighborhood)
// ---------------------------------------------------------------------------

export interface NeighborhoodNode {
  id: string;
  type: "Strain" | "Person" | "Claim" | "Source" | "Archive";
  label: string;
  data: {
    name?: string;
    slug?: string;
    handle?: string;
    platform?: string;
    display_name?: string;
    type?: "LINEAGE" | "TERPENE" | "EFFECT" | "YIELD" | "METRIC";
    value?: string;
    claimType?: string;
    trust_tier: TrustTier;
    confidence: number;
    origin?: string;
    // Human-curation provenance (present when origin === 'curated'):
    // the tier was set by a person, never by the pipeline.
    curated_tier?: TrustTier | null;
    curated_note?: string | null;
    curated_at?: number | null;
    relation?: string;
    image_url?: string;
    summary?: string;
    // Provenance of a mined summary (verbatim lead of one source snippet).
    // Null for LLM/human-written text — attribution exists only when the
    // words were mined from raw evidence.
    summary_source_url?: string | null;
    summary_source_title?: string | null;
    // Page title of a claim's source (joined from the sources table) —
    // null for provider pseudo-URLs that have no sources row.
    source_title?: string | null;
    breeder?: string;
    source_url?: string;
    excerpt?: string;
    // Freshness — epoch seconds for when the strain entered the KB and when
    // research last touched it. Absent timestamps stay absent (never faked).
    first_seen?: number | null;
    last_researched?: number | null;
    props?: Record<string, unknown>;
    [key: string]: unknown;
  };
}

export interface NeighborhoodEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  data?: Record<string, unknown>;
}

export interface NeighborhoodResponse {
  slug: string;
  depth: number;
  center_node_id: string;
  nodes: NeighborhoodNode[];
  edges: NeighborhoodEdge[];
  stats: {
    node_count: number;
    edge_count: number;
    trust_distribution: Record<string, number>;
    // DISTINCT non-empty claim source URLs across the claims in this
    // payload — how many places the dossier drew on.
    sources_consulted?: number;
    node_types: Record<string, number>;
  };
  cached_at: number;
}

// ---------------------------------------------------------------------------
// Knowledge-base stats (GET /api/v1/graph/stats) — the Dashboard overview
// ---------------------------------------------------------------------------

export interface GraphStats {
  total_nodes: number;
  total_edges: number;
  total_sources: number;
  total_claims: number;
  research_runs: number;
  node_types: Record<string, number>;
  trust_distribution: Record<string, number>;
}

// ---------------------------------------------------------------------------
// Evidence & conflict drill-down (GET /api/v1/graph/strains/{slug}/evidence
// and /conflicts) — the raw rows behind every derived verdict
// ---------------------------------------------------------------------------

/** One raw lineage observation (append-only lineage_sources row). */
export interface EvidenceObservation {
  sourceUrl: string;
  sourceTitle: string;
  engine: string;
  confidence: number;
  excerpt: string;
  observedAt: number;
  /** Captured Wayback copy of the source, when archive.org had one.
   * Absent/NULL means exactly "no capture on record". */
  waybackUrl?: string | null;
}

/** The edge aggregate plus EVERY non-quarantined observation for a parent. */
export interface EvidenceEdge {
  parent: string;
  parentName: string;
  agreement: "multi_source" | "single_source" | "disagreement" | null;
  sourceCount: number;
  domainCount: number;
  sourceDomains: string[];
  avgConfidence: number;
  observations: EvidenceObservation[];
}

export interface EvidenceResponse {
  slug: string;
  childName: string;
  edges: EvidenceEdge[];
}

/** One source backing one asserted parent-set. */
export interface ConflictSource {
  url: string;
  title: string;
  engine: string;
  observedAt: number;
}

/** One asserted parent-set ("GSC × OG Kush") and the sources asserting it. */
export interface ConflictTuple {
  parents: string[];
  parentNames: string[];
  sources: ConflictSource[];
}

/** A group of mutually-conflicting parent-set assertions. */
export interface StrainConflict {
  child: string;
  childName: string;
  summary: string;
  tuples: ConflictTuple[];
}

export interface ConflictsResponse {
  slug: string;
  conflictCount: number;
  conflicts: StrainConflict[];
}

// ---------------------------------------------------------------------------
// Curation loop (POST /graph/strains/{slug}/review and
// POST /graph/observations/quarantine) — the human path to VERIFIED
// ---------------------------------------------------------------------------

/** A strain node as returned by /graph/strains/{slug} (and the review route). */
export interface StrainNodeResponse {
  id: string;
  type: string;
  label: string;
  data: {
    name: string;
    slug: string;
    trust_tier: TrustTier;
    confidence: number;
    origin: string;
    curated_tier?: TrustTier | null;
    curated_note?: string | null;
    curated_at?: number | null;
    relation?: string;
    summary?: string | null;
    summary_source_url?: string | null;
    summary_source_title?: string | null;
    image_url?: string | null;
    props?: Record<string, unknown>;
  };
}

/** Tiers a human curator may assign. CONTRADICTED is data-derived only. */
export const CURATABLE_TIERS: TrustTier[] = [
  "VERIFIED",
  "COMMUNITY_CONSENSUS",
  "ANECDOTAL",
];

/** Updated strain + edge state after a quarantine flip. */
export interface QuarantineResponse {
  slug: string;
  name: string;
  trustTier: TrustTier | null;
  confidence: number | null;
  origin: string | null;
  curatedTier?: TrustTier | null;
  quarantined: boolean;
  edge: {
    parent: string;
    remainingObservations: number;
    agreement: string | null;
    sourceCount: number;
    domainCount: number;
  };
}

// ---------------------------------------------------------------------------
// Research run history (GET /research/runs) — read path for research_runs
// ---------------------------------------------------------------------------

export interface ResearchRunSummary {
  id: string;
  query: string;
  startedAt: number;
  completedAt: number;
  providersUsed: string[];
  sourcesCount: number;
  claimsCount: number;
  error: string | null;
}

export interface ResearchRunsResponse {
  runs: ResearchRunSummary[];
  total: number;
}

// ---------------------------------------------------------------------------
// Research stages (agent progress)
// ---------------------------------------------------------------------------

export interface StageInfo {
  agent: string;
  label: string;
  status: string;
  summary: string;
  details?: unknown[];
}

// ---------------------------------------------------------------------------
// Research lineage claim — THE one claim shape. POST /research/submit's
// run.lineage_claims and GET /research/by-strain's claims[].claim both
// speak it; nothing else may mint a second claim format.
// ---------------------------------------------------------------------------

export interface LineageClaim {
  child: string;
  parent_a: string;
  parent_b: string;
  extra_parents: string[];
  source_url: string;
  source_title: string;
  source_engine: string;
  snippet_excerpt: string;
  confidence: number;
  /** Backend-computed tier — the server is the only tier authority.
   * Missing means "not yet derived"; the frontend then shows ANECDOTAL. */
  tier?: TrustTier;
}

// ---------------------------------------------------------------------------
// API Responses
// ---------------------------------------------------------------------------

export interface HealthStatus {
  status: "ok" | "degraded" | "error";
  version: string;
  timestamp: string;
}

// ---------------------------------------------------------------------------
// Trust Tier Helpers
// ---------------------------------------------------------------------------

export const TRUST_TIER_META: Record<
  TrustTier,
  { label: string; color: string; badge: string; description: string }
> = {
  VERIFIED: {
    label: "Verified",
    color: "#D4A017",
    badge: "VERIFIED",
    description: "Lab data or verified documentation",
  },
  COMMUNITY_CONSENSUS: {
    label: "Community Consensus",
    color: "#2DD4BF",
    badge: "COMMUNITY",
    description: "3+ independent sources, temporally consistent",
  },
  ANECDOTAL: {
    label: "Anecdotal",
    color: "#94A3B8",
    badge: "ANECDOTAL",
    description: "1-2 sources, plausible but unverifiable",
  },
  CONTRADICTED: {
    label: "Contradicted",
    color: "#EF4444",
    badge: "CONTRADICTED",
    description: "Direct contradictions with no resolution",
  },
};

export function getTrustTierColor(tier?: TrustTier): string {
  if (!tier) return "#94A3B8";
  return TRUST_TIER_META[tier]?.color ?? "#94A3B8";
}

export function getTrustTierLabel(tier?: TrustTier): string {
  if (!tier) return TRUST_TIER_META.ANECDOTAL.label;
  return TRUST_TIER_META[tier]?.label ?? tier;
}

/**
 * Honest fallback for display labels on a neighborhood node: without a
 * recorded trust_tier nothing above ANECDOTAL may be asserted.
 */
export function nodeKindLabel(node: { type: string }): string {
  if (node.type === "Claim") return "CLAIM";
  if (node.type === "Person") return "BREEDER";
  return "STRAIN";
}

/**
 * Hostname of a source URL for domain chips (e.g. `seedfinder.de`).
 * Mirrors the backend's `_domain()`: lowercase, www-stripped, URL itself
 * as fallback when parsing fails.
 */
export function sourceDomain(url: string): string {
  try {
    const host = new URL(url).hostname.toLowerCase();
    return host.startsWith("www.") ? host.slice(4) : host;
  } catch {
    return url;
  }
}