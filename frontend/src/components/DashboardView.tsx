"use client";

import { useCallback, useEffect, useState } from "react";
import {
  TRUST_TIER_META,
  getTrustTierColor,
  getTrustTierLabel,
  type GraphStats,
  type ResearchRunSummary,
  type TrustTier,
} from "@/lib/types";
import { fetchGraphStats, fetchResearchRuns } from "@/lib/api-client";
import { relativeTime } from "@/lib/time";
import { normalizeSlug } from "@/lib/slug";

// Earth-palette token constants kept in sync with GraphCanvas.
const BG_DEEP = "#0F2A1F";
const BG_SURFACE = "#1F3A2F";
const BORDER = "#2A4A3A";
const TEXT_PRIMARY = "#EDE6D8";
const TEXT_MUTED = "#8B9A8E";
const TEXT_FAINT = "#5A6B5F";
// Gold accent = verified gold; sourced from the one token table.
const ACCENT = TRUST_TIER_META.VERIFIED.color;
const ACCENT_DIM = "#8B6914";

const TIER_ORDER: TrustTier[] = [
  "VERIFIED",
  "COMMUNITY_CONSENSUS",
  "ANECDOTAL",
  "CONTRADICTED",
];

const EMPTY_STATS: GraphStats = {
  total_nodes: 0,
  total_edges: 0,
  total_sources: 0,
  total_claims: 0,
  research_runs: 0,
  node_types: {},
  trust_distribution: {},
};

function StatCard({
  label,
  value,
  accent,
  sub,
}: {
  label: string;
  value: number | string;
  accent?: string;
  sub?: string;
}) {
  return (
    <div
      style={{
        background: BG_SURFACE,
        border: `1px solid ${BORDER}`,
        borderRadius: 14,
        padding: "18px 22px",
        minWidth: 0,
      }}
    >
      <div
        style={{
          fontFamily: "'EB Garamond', serif",
          fontSize: 34,
          lineHeight: 1,
          fontWeight: 500,
          color: accent ?? TEXT_PRIMARY,
          fontVariantNumeric: "tabular-nums",
        }}
      >
        {value}
      </div>
      <div
        style={{
          marginTop: 6,
          fontSize: 9,
          letterSpacing: "0.18em",
          color: TEXT_MUTED,
          textTransform: "uppercase",
        }}
      >
        {label}
      </div>
      {sub && (
        <div style={{ marginTop: 4, fontSize: 11, color: TEXT_FAINT }}>{sub}</div>
      )}
    </div>
  );
}

export default function DashboardView() {
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // ── Recent research runs (GET /research/runs) ──────────────────────
  const [runs, setRuns] = useState<ResearchRunSummary[] | null>(null);
  const [runsError, setRunsError] = useState<string | null>(null);

  const loadRuns = useCallback(() => {
    setRunsError(null);
    return fetchResearchRuns(20)
      .then((r) => setRuns(r.runs))
      .catch((e: Error) => setRunsError(e.message));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchGraphStats()
      .then((s) => {
        if (!cancelled) setStats(s);
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    loadRuns();
  }, [loadRuns]);

  const handleRefreshAll = useCallback(() => {
    setLoading(true);
    fetchGraphStats()
      .then(setStats)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
    loadRuns();
  }, [loadRuns]);

  if (loading && !stats) {
    return (
      <div
        style={{
          display: "flex",
          height: "100%",
          alignItems: "center",
          justifyContent: "center",
          color: TEXT_MUTED,
          fontFamily: "Inter, sans-serif",
          fontSize: 12,
          background: BG_DEEP,
        }}
      >
        Loading knowledge-base stats…
      </div>
    );
  }

  if (error && !stats) {
    return (
      <div
        style={{
          display: "flex",
          height: "100%",
          alignItems: "center",
          justifyContent: "center",
          color: TRUST_TIER_META.CONTRADICTED.color,
          fontFamily: "Inter, sans-serif",
          fontSize: 12,
          background: BG_DEEP,
        }}
      >
        Error loading stats: {error}
      </div>
    );
  }

  const s = stats ?? EMPTY_STATS;
  const tierTotal = Object.values(s.trust_distribution).reduce((a, b) => a + b, 0);
  const strainTypeCount = s.node_types["Strain"] ?? 0;

  return (
    <div
      style={{
        height: "100%",
        overflowY: "auto",
        background: BG_DEEP,
        backgroundImage:
          "radial-gradient(circle at 30% 20%, rgba(212,160,23,0.05), transparent 55%), radial-gradient(circle at 75% 80%, rgba(45,212,191,0.05), transparent 55%)",
        padding: "34px 40px 60px",
      }}
    >
      <div className="mx-auto w-full max-w-[1120px]">
      {/* Header */}
      <div style={{ marginBottom: 22 }}>
        <div
          style={{
            fontSize: 9,
            letterSpacing: "0.22em",
            color: ACCENT_DIM,
            textTransform: "uppercase",
            marginBottom: 8,
          }}
        >
          Knowledge Base · Overview
        </div>
        <h2
          style={{
            fontFamily: "'EB Garamond', serif",
            fontSize: 30,
            fontWeight: 500,
            color: TEXT_PRIMARY,
            margin: 0,
            lineHeight: 1.05,
          }}
        >
          The Accumulating Record
        </h2>
        <div
          style={{
            fontFamily: "'EB Garamond', serif",
            fontStyle: "italic",
            fontSize: 13,
            color: TEXT_MUTED,
            marginTop: 6,
          }}
        >
          Every strain here arrived via live web research — nothing pre-seeded.
        </div>
      </div>

      {/* Top stat cards */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns:
            "repeat(auto-fit, minmax(150px, 1fr))",
          gap: 14,
          marginBottom: 30,
        }}
      >
        <StatCard label="Strains" value={strainTypeCount} accent={ACCENT} />
        <StatCard label="Lineage Edges" value={s.total_edges} accent={TRUST_TIER_META.COMMUNITY_CONSENSUS.color} />
        <StatCard label="Sources" value={s.total_sources} />
        <StatCard label="Claims" value={s.total_claims} />
        <StatCard label="Research Runs" value={s.research_runs} />
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 18,
        }}
      >
        {/* Trust distribution */}
        <div
          style={{
            background: BG_SURFACE,
            border: `1px solid ${BORDER}`,
            borderRadius: 14,
            padding: "20px 24px",
          }}
        >
          <div
            style={{
              fontSize: 9,
              letterSpacing: "0.18em",
              color: TEXT_MUTED,
              textTransform: "uppercase",
              marginBottom: 16,
            }}
          >
            Trust Distribution
          </div>
          {TIER_ORDER.map((tier) => {
            const count = s.trust_distribution[tier] ?? 0;
            const pct = tierTotal > 0 ? (count / tierTotal) * 100 : 0;
            const color = getTrustTierColor(tier);
            return (
              <div key={tier} style={{ marginBottom: 14 }}>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    fontSize: 11,
                    color: TEXT_MUTED,
                    marginBottom: 6,
                  }}
                >
                  <span style={{ color, textTransform: "uppercase", letterSpacing: "0.06em" }}>
                    {getTrustTierLabel(tier)}
                  </span>
                  <span style={{ fontVariantNumeric: "tabular-nums" }}>
                    {count}
                  </span>
                </div>
                <div
                  style={{
                    height: 6,
                    background: BG_DEEP,
                    borderRadius: 4,
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      height: "100%",
                      width: `${pct}%`,
                      background: color,
                      opacity: 0.75,
                      borderRadius: 4,
                    }}
                  />
                </div>
              </div>
            );
          })}
          {tierTotal === 0 && (
            <div style={{ color: TEXT_FAINT, fontSize: 12 }}>
              No evidence yet — run a search to begin the record.
            </div>
          )}
        </div>

        {/* Node types + note */}
        <div
          style={{
            background: BG_SURFACE,
            border: `1px solid ${BORDER}`,
            borderRadius: 14,
            padding: "20px 24px",
          }}
        >
          <div
            style={{
              fontSize: 9,
              letterSpacing: "0.18em",
              color: TEXT_MUTED,
              textTransform: "uppercase",
              marginBottom: 16,
            }}
          >
            Graph Breakdown
          </div>
          {Object.entries({
            Strain: s.node_types["Strain"] ?? 0,
            Person: s.node_types["Person"] ?? 0,
            Claim: s.node_types["Claim"] ?? s.total_claims,
          }).map(([label, value]) => (
            <div
              key={label}
              style={{ display: "flex", justifyContent: "space-between", padding: "6px 0", borderBottom: `1px solid ${BORDER}`, fontSize: 13, color: TEXT_PRIMARY }}
            >
              <span style={{ color: TEXT_MUTED }}>{label}</span>
              <span style={{ fontVariantNumeric: "tabular-nums" }}>{value}</span>
            </div>
          ))}
          <div
            style={{
              marginTop: 16,
              fontFamily: "'EB Garamond', serif",
              fontStyle: "italic",
              fontSize: 12.5,
              lineHeight: 1.5,
              color: TEXT_FAINT,
            }}
          >
            {strainTypeCount > 0
              ? `${strainTypeCount} strains are citizen-researched. Revisit any name in the search bar and its graph renders instantly from the cache.`
              : "The graph is empty until you research a strain."}
          </div>
        </div>
      </div>

      {/* ── Recent research runs — the read path for research_runs ── */}
      <div
        style={{
          marginTop: 18,
          background: BG_SURFACE,
          border: `1px solid ${BORDER}`,
          borderRadius: 14,
          padding: "20px 24px",
        }}
      >
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            marginBottom: 16,
          }}
        >
          <div
            style={{
              fontSize: 9,
              letterSpacing: "0.18em",
              color: TEXT_MUTED,
              textTransform: "uppercase",
            }}
          >
            Recent Research Runs
          </div>
          <button
            type="button"
            onClick={handleRefreshAll}
            disabled={loading && !stats}
            className="cursor-pointer rounded-md border bg-transparent px-2.5 py-1 text-[9px] uppercase tracking-[0.12em] transition-colors hover:opacity-80 disabled:opacity-50"
            style={{ borderColor: BORDER, color: TEXT_MUTED, fontFamily: "Inter, sans-serif" }}
            title="Refresh stats and run history"
          >
            ↻ Refresh
          </button>
        </div>

        {runsError && (
          <div style={{ color: TRUST_TIER_META.CONTRADICTED.color, fontSize: 12 }}>
            Could not load run history: {runsError}
          </div>
        )}

        {!runsError && runs === null && (
          <div style={{ color: TEXT_MUTED, fontSize: 12, display: "flex", alignItems: "center", gap: 8 }}>
            <span
              className="h-3 w-3 animate-spin rounded-full border-2"
              style={{ borderColor: `${ACCENT} transparent ${ACCENT} transparent` }}
            />
            Loading run history…
          </div>
        )}

        {!runsError && runs !== null && runs.length === 0 && (
          <div style={{ color: TEXT_FAINT, fontSize: 12 }}>No research runs yet.</div>
        )}

        {!runsError && runs !== null && runs.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column" }}>
            {runs.map((run) => (
              <a
                key={run.id}
                href={`/?strain=${encodeURIComponent(normalizeSlug(run.query))}`}
                className="cursor-pointer"
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: 14,
                  padding: "9px 0",
                  borderBottom: `1px solid ${BORDER}`,
                  textDecoration: "none",
                  fontSize: 12.5,
                  color: TEXT_PRIMARY,
                }}
                title={`Open the ${run.query} dossier`}
              >
                <span
                  style={{
                    flex: "1 1 auto",
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    fontFamily: "'EB Garamond', serif",
                    fontStyle: "italic",
                    fontSize: 15,
                  }}
                >
                  {run.query || "(unnamed query)"}
                </span>
                {run.error && (
                  <span
                    style={{
                      flexShrink: 0,
                      fontSize: 8.5,
                      letterSpacing: "0.08em",
                      textTransform: "uppercase",
                      color: TRUST_TIER_META.CONTRADICTED.color,
                      border: `1px solid ${TRUST_TIER_META.CONTRADICTED.color}66`,
                      borderRadius: 6,
                      padding: "1px 6px",
                    }}
                    title={run.error}
                  >
                    Failed
                  </span>
                )}
                <span style={{ flexShrink: 0, color: TEXT_MUTED, fontVariantNumeric: "tabular-nums" }}>
                  {run.claimsCount} claim{run.claimsCount === 1 ? "" : "s"} ·{" "}
                  {run.sourcesCount} source{run.sourcesCount === 1 ? "" : "s"}
                </span>
                <span style={{ flexShrink: 0, color: TEXT_FAINT, fontSize: 11 }}>
                  {run.providersUsed.length > 0 ? run.providersUsed.join(" · ") : "no providers"}
                </span>
                <span style={{ flexShrink: 0, color: TEXT_FAINT, fontSize: 11, width: 64, textAlign: "right" }}>
                  {relativeTime(run.startedAt)}
                </span>
              </a>
            ))}
            <div style={{ marginTop: 10, color: TEXT_FAINT, fontSize: 11 }}>
              {runs.length} of the most recent runs shown — open any row to view
              that strain&apos;s dossier.
            </div>
          </div>
        )}
      </div>
      </div>
    </div>
  );
}
