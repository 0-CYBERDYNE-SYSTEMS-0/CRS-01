// =============================================================================
// CRS-01 API Client — connects frontend to FastAPI backend
// =============================================================================

import type {
  HealthStatus,
  LineageClaim,
  TrustTier,
  NeighborhoodResponse,
  EvidenceResponse,
  ConflictsResponse,
  StageInfo,
  GraphStats,
  StrainNodeResponse,
  QuarantineResponse,
  ResearchRunsResponse,
} from "./types";

// Same-origin by default: next.config.ts rewrites /api/v1/* and /health to
// the backend (the SPEC's proxy design), so the app works unchanged on
// localhost, in dev, and through a reverse proxy like `tailscale serve` —
// no CORS involved. Set NEXT_PUBLIC_API_URL to call a backend elsewhere.
const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "/api/v1";

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export async function fetchHealth(): Promise<HealthStatus> {
  const res = await fetch(`${API_BASE.replace("/api/v1", "")}/health`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Health check failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Neighborhood — k-hop subgraph around a strain, the primary tester view
// ---------------------------------------------------------------------------

export async function fetchNeighborhood(
  slug: string,
  depth: number = 2
): Promise<NeighborhoodResponse> {
  const res = await fetch(
    `${API_BASE}/graph/strains/${encodeURIComponent(slug)}/neighborhood?depth=${depth}`,
    { cache: "no-store" }
  );
  if (!res.ok) {
    if (res.status === 404) {
      let suggestions: Array<{ slug: string; name: string; confidence: number }> = [];
      try {
        const body = await res.json();
        if (typeof body.detail === "object" && Array.isArray(body.detail.suggestions)) {
          suggestions = body.detail.suggestions;
        }
      } catch { /* ignore parse errors */ }
      const err: Error & { suggestions?: typeof suggestions } = new Error(
        `Strain '${slug}' not found`
      );
      err.suggestions = suggestions;
      throw err;
    }
    throw new Error(`Neighborhood fetch failed: ${res.status}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// Evidence & conflicts — drill-down into the raw rows behind every verdict
// ---------------------------------------------------------------------------

export async function fetchEdgeEvidence(
  slug: string
): Promise<EvidenceResponse | null> {
  const res = await fetch(
    `${API_BASE}/graph/strains/${encodeURIComponent(slug)}/evidence`,
    { cache: "no-store" }
  );
  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(`Evidence fetch failed: ${res.status}`);
  }
  const d = await res.json();
  // Backend returns snake_case; normalize to camelCase for the frontend.
  return {
    slug: d.slug ?? slug,
    childName: d.child_name ?? d.childName ?? slug,
    edges: (Array.isArray(d.edges) ? d.edges : []).map(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (e: any) => ({
        parent: e.parent ?? "",
        parentName: e.parent_name ?? e.parentName ?? e.parent ?? "",
        agreement: e.agreement ?? null,
        sourceCount: e.source_count ?? 0,
        domainCount: e.domain_count ?? 0,
        sourceDomains: Array.isArray(e.source_domains) ? e.source_domains : [],
        avgConfidence: e.avg_confidence ?? 0,
        observations: (Array.isArray(e.observations) ? e.observations : []).map(
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (o: any) => ({
            sourceUrl: o.source_url ?? "",
            sourceTitle: o.source_title ?? "",
            engine: o.engine ?? "",
            confidence: o.confidence ?? 0,
            excerpt: o.excerpt ?? "",
            observedAt: o.observed_at ?? 0,
            // Archived copy of the source when one was captured; null means
            // exactly "no capture on record", never "we didn't check".
            waybackUrl: o.wayback_url ?? null,
          })
        ),
      })
    ),
  };
}

export async function fetchConflicts(
  slug: string
): Promise<ConflictsResponse | null> {
  const res = await fetch(
    `${API_BASE}/graph/strains/${encodeURIComponent(slug)}/conflicts`,
    { cache: "no-store" }
  );
  if (!res.ok) {
    if (res.status === 404) return null;
    throw new Error(`Conflicts fetch failed: ${res.status}`);
  }
  const d = await res.json();
  // Backend returns snake_case; normalize to camelCase for the frontend.
  return {
    slug: d.slug ?? slug,
    conflictCount: d.conflict_count ?? d.conflictCount ?? 0,
    conflicts: (Array.isArray(d.conflicts) ? d.conflicts : []).map(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (c: any) => ({
        child: c.child ?? slug,
        childName: c.child_name ?? c.childName ?? "",
        summary: c.summary ?? "",
        tuples: (Array.isArray(c.tuples) ? c.tuples : []).map(
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          (t: any) => ({
            parents: Array.isArray(t.parents) ? t.parents : [],
            parentNames: Array.isArray(t.parent_names)
              ? t.parent_names
              : Array.isArray(t.parentNames)
              ? t.parentNames
              : [],
            sources: (Array.isArray(t.sources) ? t.sources : []).map(
              // eslint-disable-next-line @typescript-eslint/no-explicit-any
              (s: any) => ({
                url: s.url ?? "",
                title: s.title ?? "",
                engine: s.engine ?? "",
                observedAt: s.observed_at ?? s.observedAt ?? 0,
              })
            ),
          })
        ),
      })
    ),
  };
}

// ---------------------------------------------------------------------------
// Curation — the human loop. VERIFIED only ever comes from a person.
// ---------------------------------------------------------------------------

/** Shared detail-message extraction for curation endpoints (they 400/404
 * with either a string detail or {message, suggestions}). */
async function curationError(res: Response, fallback: string): Promise<Error> {
  try {
    const body = await res.json();
    const detail = body?.detail;
    if (typeof detail === "string" && detail) return new Error(detail);
    if (detail?.message) return new Error(detail.message);
  } catch { /* ignore parse errors */ }
  return new Error(`${fallback}: ${res.status}`);
}

export async function submitStrainReview(
  slug: string,
  tier: TrustTier | null,
  note: string = ""
): Promise<StrainNodeResponse> {
  const res = await fetch(
    `${API_BASE}/graph/strains/${encodeURIComponent(slug)}/review`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tier, note }),
    }
  );
  if (!res.ok) {
    throw await curationError(res, "Review failed");
  }
  return res.json();
}

export async function quarantineObservation(
  childSlug: string,
  parentSlug: string,
  sourceUrl: string,
  quarantined: boolean = true
): Promise<QuarantineResponse> {
  const res = await fetch(`${API_BASE}/graph/observations/quarantine`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      child_slug: childSlug,
      parent_slug: parentSlug,
      source_url: sourceUrl,
      quarantined,
    }),
  });
  if (!res.ok) {
    throw await curationError(res, "Quarantine failed");
  }
  const d = await res.json();
  // Backend returns snake_case; normalize to camelCase for the frontend.
  return {
    slug: d.slug ?? childSlug,
    name: d.name ?? childSlug,
    trustTier: d.trust_tier ?? null,
    confidence: d.confidence ?? null,
    origin: d.origin ?? null,
    curatedTier: d.curated_tier ?? null,
    quarantined: d.quarantined ?? quarantined,
    edge: {
      parent: d.edge?.parent ?? parentSlug,
      remainingObservations: d.edge?.remaining_observations ?? 0,
      agreement: d.edge?.agreement ?? null,
      sourceCount: d.edge?.source_count ?? 0,
      domainCount: d.edge?.domain_count ?? 0,
    },
  };
}

// ---------------------------------------------------------------------------
// Strain catalog — listing + search (used by the search bar autocomplete
// and the suggestion chips under the hero copy).
// ---------------------------------------------------------------------------

export interface StrainListItem {
  id: string;
  name: string;
  slug: string;
  trust_tier: TrustTier;
  confidence: number;
  props?: Record<string, unknown>;
}

export interface StrainSearchMatch {
  id: string;
  name: string;
  slug: string;
  trust_tier: TrustTier;
  confidence: number;
}

export async function listStrains(
  limit: number = 50,
  offset: number = 0
): Promise<{ total: number; limit: number; offset: number; items: StrainListItem[] }> {
  const res = await fetch(
    `${API_BASE}/graph/strains?limit=${limit}&offset=${offset}`,
    { cache: "no-store" }
  );
  if (!res.ok) throw new Error(`List strains failed: ${res.status}`);
  return res.json();
}

export async function searchStrains(
  q: string,
  limit: number = 8
): Promise<{ query: string; matches: StrainSearchMatch[] }> {
  const res = await fetch(
    `${API_BASE}/graph/strains/search?q=${encodeURIComponent(q)}&limit=${limit}`,
    { cache: "no-store" }
  );
  if (!res.ok) throw new Error(`Search strains failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Knowledge-base stats — powers the Dashboard overview tab
// ---------------------------------------------------------------------------

export async function fetchGraphStats(): Promise<GraphStats> {
  const res = await fetch(`${API_BASE}/graph/stats`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Graph stats failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Deep research — fan out across providers, extract lineage, return graph
// ---------------------------------------------------------------------------

export interface ResearchSource {
  title: string;
  url: string;
  engine: string;
}

export interface ResearchRun {
  query: string;
  run_id: string;
  started_at: number;
  completed_at: number;
  providers_used: string[];
  sources_visited: ResearchSource[];
  lineage_claims: LineageClaim[];
  consensus: Record<string, { sources: unknown[]; count: number; avg_confidence: number }>;
  disagreements: Array<{ child: string; tuples: unknown[]; summary: string }>;
  lineage_graph: {
    nodes: Array<{ id: string; type: string; data: Record<string, unknown> }>;
    edges: Array<{
      id: string;
      source: string;
      target: string;
      data: Record<string, unknown>;
    }>;
    node_count: number;
    edge_count: number;
  };
  stages: StageInfo[];
  strain_meta: Record<string, Record<string, unknown>>;
  raw_dir?: string | null;
  error?: string | null;
}

export interface ResearchSubmitResponse {
  run: ResearchRun;
  neighborhood: NeighborhoodResponse | null;
}

export async function submitResearch(
  query: string,
  maxDepth: number = 1,
  maxNodes: number = 12
): Promise<ResearchSubmitResponse> {
  const res = await fetch(`${API_BASE}/research/submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, max_depth: maxDepth, max_nodes: maxNodes }),
  });
  if (!res.ok) {
    const errText = await res.text().catch(() => "Unknown error");
    throw new Error(`Research submit failed (${res.status}): ${errText}`);
  }
  const data = await res.json();
  return data as ResearchSubmitResponse;
}

export async function fetchResearchByStrain(
  slug: string,
  limit: number = 20
): Promise<{
  slug: string;
  claims: Array<{ kind: string; run_id: string; claim: LineageClaim; ingested_at: number }>;
  total: number;
  ledger_path: string;
}> {
  const res = await fetch(
    `${API_BASE}/research/by-strain?slug=${encodeURIComponent(slug)}&limit=${limit}`,
    { cache: "no-store" }
  );
  if (!res.ok) {
    if (res.status === 404) {
      return { slug, claims: [], total: 0, ledger_path: "" };
    }
    throw new Error(`Research by-strain failed: ${res.status}`);
  }
  return res.json();
}

export async function fetchResearchRuns(
  limit: number = 20
): Promise<ResearchRunsResponse> {
  const res = await fetch(`${API_BASE}/research/runs?limit=${limit}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Research runs fetch failed: ${res.status}`);
  const d = await res.json();
  // Backend returns snake_case; normalize to camelCase for the frontend.
  return {
    total: d.total ?? 0,
    runs: (Array.isArray(d.runs) ? d.runs : []).map(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (r: any) => ({
        id: r.id ?? "",
        query: r.query ?? "",
        startedAt: r.started_at ?? 0,
        completedAt: r.completed_at ?? 0,
        providersUsed: Array.isArray(r.providers_used) ? r.providers_used : [],
        sourcesCount: r.sources_count ?? 0,
        claimsCount: r.claims_count ?? 0,
        error: r.error ?? null,
      })
    ),
  };
}