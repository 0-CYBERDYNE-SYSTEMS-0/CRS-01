"use client";

import { useState, useCallback, useEffect, Suspense, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import {
  fetchHealth,
  fetchNeighborhood,
  fetchResearchByStrain,
  submitResearch,
  listStrains,
  type StrainListItem,
  type StrainSearchMatch,
  type ResearchSubmitResponse,
} from "@/lib/api-client";
import GraphCanvas from "@/components/GraphCanvas";
import NodeDetailCard from "@/components/NodeDetailCard";
import ReportView from "@/components/ReportView";
import SourcesView from "@/components/SourcesView";
import ResearchingPanel from "@/components/ResearchingPanel";
import SearchAutocomplete from "@/components/SearchAutocomplete";
import SuggestionChips from "@/components/SuggestionChips";
import EmptyState from "@/components/EmptyState";
import ViewSwitcher, { type CanvasView } from "@/components/ViewSwitcher";
import DashboardView from "@/components/DashboardView";
import MethodologyDrawer from "@/components/MethodologyDrawer";
import SubjectRail from "@/components/SubjectRail";
import type {
  HealthStatus,
  LineageClaim,
  StageInfo,
  NeighborhoodResponse,
  TrustTier,
} from "@/lib/types";
import { getTrustTierColor, getTrustTierLabel } from "@/lib/types";

const DEFAULT_STRAIN = "gmo";
const SUGGESTION_LIMIT = 6;

export default function HomePage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center text-[var(--text-muted)]">
          Loading…
        </div>
      }
    >
      <HomePageInner />
    </Suspense>
  );
}

function HomePageInner() {
  const searchParams = useSearchParams();

  // ── Strain the canvas is currently showing ─────────────────────────
  const [activeStrain, setActiveStrain] = useState<string>(
    () => searchParams.get("strain")?.trim() || DEFAULT_STRAIN
  );

  // ── Search bar input (controlled, separate from activeStrain) ───────
  const [searchValue, setSearchValue] = useState<string>(
    () => searchParams.get("strain")?.trim() || ""
  );

  // ── Canvas data state ──────────────────────────────────────────────
  const [neighborhood, setNeighborhood] = useState<NeighborhoodResponse | null>(null);
  const [neighborhoodLoading, setNeighborhoodLoading] = useState(false);
  const [neighborhoodError, setNeighborhoodError] = useState<string | null>(null);
  const [notFoundSuggestions, setNotFoundSuggestions] = useState<
    Array<{ slug: string; name: string; confidence: number }> | null
  >(null);

  // ── Active view tab (Graph · Report · Sources · Dashboard) ────────
  // Initialized from ?view= so each showcase view has a shareable URL.
  const [view, setView] = useState<CanvasView>(() => {
    const v = searchParams.get("view");
    return v === "report" || v === "sources" || v === "dashboard" ? v : "graph";
  });

  // ── Methodology drawer (the "?" beside the view tabs) ──────────────
  const [methodologyOpen, setMethodologyOpen] = useState(false);

  // Phone sheet for the strain rail. Desktop keeps the rail in the column;
  // below md the same body opens from the peek bar (and on node select).
  const [railSheetOpen, setRailSheetOpen] = useState(false);
  const narrow = useIsNarrow();

  // ── Selected wheel node — its detail card renders in the rail ──────
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // ── Bumped by curation actions (quarantine / review) so the
  //    neighborhood — and every derived tier on it — visibly recomputes.
  const [kbVersion, setKbVersion] = useState(0);
  const refreshKb = useCallback(() => setKbVersion((v) => v + 1), []);

  // ── Sync ?view= in the URL when the tab changes ──────────────────
  useEffect(() => {
    const url = new URL(window.location.href);
    if (view !== "graph") {
      url.searchParams.set("view", view);
    } else {
      url.searchParams.delete("view");
    }
    window.history.replaceState(null, "", url.toString());
  }, [view]);

  useEffect(() => {
    if (!railSheetOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setRailSheetOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [railSheetOpen]);

  useEffect(() => {
    if (view !== "graph") setRailSheetOpen(false);
  }, [view]);

  // ── Research state — auto-triggered on unknown strains ─────────────
  // Claims speak the ONE research lineage-claim shape, whether they came
  // from a live run or the ledger's by-strain read.
  const [researchClaims, setResearchClaims] = useState<LineageClaim[] | null>(null);
  const [researching, setResearching] = useState(false);
  const [researchError, setResearchError] = useState<string | null>(null);
  const [researchProvider, setResearchProvider] = useState<string | null>(null);
  const [researchFromLedger, setResearchFromLedger] = useState(false);
  const [researchStages, setResearchStages] = useState<StageInfo[] | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);

  // ── Health / status ────────────────────────────────────────────────
  const [healthStatus, setHealthStatus] = useState<HealthStatus | null>(null);

  // ── Suggestion chips in the toolbar ────────────────────────────────
  const [suggestions, setSuggestions] = useState<StrainListItem[] | null>(null);

  // ── Sync ?strain= in the URL when activeStrain changes ─────────────
  useEffect(() => {
    const url = new URL(window.location.href);
    if (activeStrain && activeStrain !== DEFAULT_STRAIN) {
      url.searchParams.set("strain", activeStrain);
    } else {
      url.searchParams.delete("strain");
    }
    window.history.replaceState(null, "", url.toString());
  }, [activeStrain]);

  // ── Reset research + selection state when strain changes ─────────
  useEffect(() => {
    setResearchClaims(null);
    setResearching(false);
    setResearchError(null);
    setResearchProvider(null);
    setResearchFromLedger(false);
    setResearchStages(null);
    setPanelOpen(false);
    setNotFoundSuggestions(null);
    setSelectedId(null);
  }, [activeStrain]);

  // ── Fetch the neighborhood whenever activeStrain changes ───────────
  useEffect(() => {
    if (!activeStrain) return;
    let cancelled = false;
    setNeighborhoodLoading(true);
    setNeighborhoodError(null);
    setNotFoundSuggestions(null);

    fetchNeighborhood(activeStrain, 2)
      .then((r) => {
        if (cancelled) return;
        setNeighborhood(r);
      })
      .catch((e: Error & { suggestions?: Array<{ slug: string; name: string; confidence: number }> }) => {
        if (cancelled) return;
        setNeighborhood(null);

        const suggs = e.suggestions || [];
        setNotFoundSuggestions(suggs.length ? suggs : null);

        // Phase B: the research ledger may already hold claims for this
        // strain — same lineage-claim shape a live run produces.
        fetchResearchByStrain(activeStrain)
          .then((r2) => {
            if (cancelled) return;
            if (r2.claims && r2.claims.length > 0) {
              setResearchClaims(r2.claims.map((row) => row.claim));
              setResearchFromLedger(true);
              setResearchProvider("ledger");
            }
          })
          .catch(() => { /* best-effort */ });

        // ── Auto-trigger research — no button click required. The
        // `researching` guard inside performResearch makes re-entrant
        // calls (effect re-runs while a run is in flight) no-ops. ─────
        setNeighborhoodError(e.message || "Could not load strain");
        performResearch(activeStrain);
      })
      .finally(() => {
        if (!cancelled) setNeighborhoodLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // performResearch omitted: its `researching` guard no-ops re-entry.
    // Listing it would refetch the neighborhood whenever that flag flips.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeStrain, kbVersion]);

  // ── Fetch health on mount ──────────────────────────────────────────
  useEffect(() => {
    fetchHealth()
      .then(setHealthStatus)
      .catch(() =>
        setHealthStatus({
          status: "error",
          version: "0.1.0",
          timestamp: new Date().toISOString(),
        })
      );
  }, []);

  // ── Fetch suggestion chips on mount ────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    listStrains(50, 0)
      .then((r) => {
        if (cancelled) return;
        setSuggestions(r.items.slice(0, SUGGESTION_LIMIT));
      })
      .catch(() => {
        if (!cancelled) setSuggestions([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // ── Esc closes the node detail card ────────────────────────────────
  useEffect(() => {
    if (!selectedId) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelectedId(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selectedId]);

  // ── Core research loop (called automatically + manually) ─────────
  const performResearch = useCallback(
    async (strain: string) => {
      if (!strain || researching) return;
      setResearching(true);
      setResearchError(null);
      setResearchClaims(null);
      setResearchProvider(null);
      setResearchStages(null);

      try {
        const res: ResearchSubmitResponse = await submitResearch(strain, 2, 30);
        const run = res.run;

        // lineage_claims already speak the one claim shape the panel
        // renders — no mapping, no second format. A missing tier falls
        // back to ANECDOTAL at render time; the frontend never re-derives.
        setResearchClaims(run.lineage_claims);

        setResearchProvider(run.providers_used.join("+"));
        setResearchStages(run.stages || []);
        setPanelOpen(true);

        // If the backend returned a neighborhood from the KB, swap
        // the canvas to it immediately.
        if (res.neighborhood) {
          setNeighborhood(res.neighborhood);
          setNeighborhoodError(null);
          setNotFoundSuggestions(null);
        } else {
          setNeighborhoodError(`Research completed — ${run.lineage_claims.length} claims, but no graph could be built`);
        }

        // Refresh suggestion chips so the new strain appears.
        listStrains(50, 0).then((r) =>
          setSuggestions(r.items.slice(0, SUGGESTION_LIMIT))
        ).catch(() => {});
      } catch (e: unknown) {
        setResearchError(e instanceof Error ? e.message : "Research failed");
        setNeighborhoodError(e instanceof Error ? e.message : "Could not load strain");
      } finally {
        setResearching(false);
      }
    },
    [researching]
  );

  // ── Handlers ───────────────────────────────────────────────────────
  const handleSearchSubmit = useCallback(
    (override?: string) => {
      const q = (override ?? searchValue).trim();
      if (!q) return;
      setSearchValue(q);
      setActiveStrain(q);
    },
    [searchValue]
  );

  const handleSuggestion = useCallback((slug: string) => {
    setSearchValue(slug);
    setActiveStrain(slug);
  }, []);

  const handleAutocompleteSelect = useCallback((match: StrainSearchMatch) => {
    setSearchValue(match.slug);
    setActiveStrain(match.slug);
  }, []);

  const checkHealth = useCallback(() => {
    fetchHealth().then(setHealthStatus).catch(() => {
      setHealthStatus({
        status: "error",
        version: "0.1.0",
        timestamp: new Date().toISOString(),
      });
    });
  }, []);

  const handleRetry = useCallback(() => {
    if (!activeStrain) return;
    // Bump the kbVersion nonce — the neighborhood effect re-fetches and,
    // on a 404, re-tries ledger + research for the current strain.
    refreshKb();
  }, [activeStrain, refreshKb]);

  const handleResearchClick = useCallback(() => {
    if (!activeStrain) return;
    setPanelOpen(false);
    performResearch(activeStrain);
  }, [activeStrain, performResearch]);

  const handleResearchDismiss = useCallback(() => {
    setResearchClaims(null);
    setResearchError(null);
    setResearchProvider(null);
    setResearchStages(null);
    setPanelOpen(false);
  }, []);

  const handleNotfoundSuggestion = useCallback((slug: string) => {
    setSearchValue(slug);
    setActiveStrain(slug);
  }, []);

  // ── Derived rail state ─────────────────────────────────────────────
  const selectedNode =
    neighborhood && selectedId
      ? neighborhood.nodes.find((n) => n.id === selectedId) ?? null
      : null;
  const showResearchPanel =
    view === "graph" && (researching || (panelOpen && researchClaims !== null));
  const claimCount = researchClaims?.length ?? 0;
  const claimSource = researchFromLedger
    ? "from ledger"
    : researchProvider
    ? `via ${researchProvider}`
    : "researched";

  const openReport = useCallback(() => {
    setRailSheetOpen(false);
    setView("report");
  }, []);

  const handleSelectNode = useCallback((id: string | null) => {
    setSelectedId(id);
    if (id) setRailSheetOpen(true);
  }, []);

  const peekNode =
    selectedNode ??
    neighborhood?.nodes.find((n) => n.id === neighborhood.center_node_id) ??
    null;
  const peekName = String(peekNode?.data.name ?? peekNode?.label ?? activeStrain);
  const peekTier = peekNode?.data.trust_tier as TrustTier | undefined;
  const showRailPeek =
    view === "graph" && !railSheetOpen && (Boolean(neighborhood) || showResearchPanel);
  // Until the viewport is measured, keep the desktop rail mounted (it is
  // CSS-hidden below md) so a phone open does not flash a 340px column.
  // Once we know it is narrow, unmount that copy so the sheet is the only one.
  const mountDesktopRail = narrow !== true;
  const mountMobileSheet = narrow === true && railSheetOpen && view === "graph";

  const railBody = showResearchPanel ? (
    <ResearchingPanel
      strain={activeStrain}
      claims={researchClaims ?? []}
      loading={researching}
      error={researchError}
      provider={researchProvider ?? undefined}
      fromLedger={researchFromLedger}
      stages={researchStages ?? undefined}
      onDismiss={handleResearchDismiss}
      onRetry={handleResearchClick}
    />
  ) : selectedNode && neighborhood ? (
    <NodeDetailCard
      node={selectedNode}
      response={neighborhood}
      edgeCount={
        neighborhood.edges.filter(
          (e) => e.source === selectedNode.id || e.target === selectedNode.id
        ).length
      }
      onClose={() => setSelectedId(null)}
      onOpenReport={openReport}
    />
  ) : neighborhood ? (
    <SubjectRail
      response={neighborhood}
      strain={activeStrain}
      researching={researching}
      claimCount={claimCount}
      claimSource={claimSource}
      onOpenReport={openReport}
      onReview={() => setPanelOpen(true)}
      onDeepen={handleResearchClick}
      onSelect={handleSelectNode}
      onDataChanged={refreshKb}
    />
  ) : null;

  return (
  <div className="flex h-screen flex-col overflow-hidden">
    {/* ── Header ──────────────────────────────────────────── */}
    <header className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] bg-[var(--bg-deep)]/90 px-3 py-2 sm:gap-4 sm:px-6 sm:py-2.5">
      <div className="flex shrink-0 items-center gap-2 sm:gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] text-base font-medium text-[var(--accent)]">
          S
        </div>
        <div className="hidden min-[400px]:block">
          <div className="text-[15px] font-medium tracking-tight text-[var(--text-primary)]">
            CRS-01
          </div>
          <div className="hidden text-[9.5px] uppercase tracking-[0.06em] text-[var(--text-muted)] sm:block">
            Cannabis Research Sentinel
          </div>
        </div>
      </div>

      <div className="flex min-w-0 flex-1 items-center justify-end gap-2">
        <div className="min-w-0 flex-1 sm:max-w-[280px]">
          <SearchAutocomplete
            value={searchValue}
            onChange={setSearchValue}
            onSubmit={handleSearchSubmit}
            onSelectMatch={handleAutocompleteSelect}
            placeholder="Strain name or breeder handle…"
          />
        </div>
        {healthStatus && (
          <div className="hidden items-center gap-2 rounded-full border border-[var(--border)] bg-[var(--bg-surface)]/60 px-3 py-1.5 text-[10.5px] text-[var(--text-muted)] sm:flex">
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{
                background:
                  healthStatus.status === "error"
                    ? "var(--trust-anecdotal)"
                    : "var(--trust-community)",
              }}
            />
            {healthStatus.status === "error" ? "KB offline" : "KB online"} ·
            v{healthStatus.version}
          </div>
        )}
        <button
          onClick={checkHealth}
          title="Check system health"
          className="flex shrink-0 cursor-pointer items-center rounded-lg border border-[var(--border)] bg-transparent px-3 py-2 text-xs text-[var(--text-muted)] hover:bg-[var(--bg-surface)]"
        >
          ⚙
        </button>
      </div>
    </header>

    {/* ── Toolbar: views + methodology + strain shortcuts ─── */}
    <div className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--border)] bg-[var(--bg-deep)]/60 px-3 py-2 sm:gap-4 sm:px-4">
      {/* The "?" and Stats sit beside (not inside) the tablist so the
          ViewSwitcher's role="tablist" stays intact. */}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <div className="min-w-0 overflow-x-auto">
          <ViewSwitcher view={view} onChange={setView} reportBadge={claimCount} />
        </div>
        <button
          type="button"
          onClick={() => setMethodologyOpen(true)}
          aria-label="Open methodology — how CRS-01 decides what to show"
          title="Methodology"
          className="flex h-8 w-8 shrink-0 cursor-pointer items-center justify-center rounded-full border border-[var(--border)] bg-transparent text-[13px] text-[var(--text-muted)] transition-colors hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
        >
          ?
        </button>
        <button
          type="button"
          onClick={() => setView(view === "dashboard" ? "graph" : "dashboard")}
          aria-pressed={view === "dashboard"}
          aria-label="Dashboard — graph totals, not a strain page"
          title="Dashboard — graph totals, not a strain page"
          className={`flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-full border px-2.5 text-[11px] uppercase tracking-[0.08em] transition-colors ${
            view === "dashboard"
              ? "border-[var(--accent)] bg-[var(--accent)] text-[#0C120E]"
              : "border-[var(--border)] bg-transparent text-[var(--text-muted)] hover:border-[var(--accent)] hover:text-[var(--text-primary)]"
          }`}
        >
          <span aria-hidden>◫</span>
          <span className="hidden sm:inline">Stats</span>
        </button>
      </div>
      <div className="flex items-center gap-4">
        {researching && (
          <span className="hidden items-center gap-2 rounded-full border border-[var(--border)] px-3 py-1 text-[10px] uppercase tracking-[0.1em] text-[var(--text-muted)] md:flex">
            <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--accent)]" />
            Researching
          </span>
        )}
        {suggestions && suggestions.length > 0 && (
          <div className="hidden lg:block">
            <SuggestionChips
              suggestions={suggestions}
              onSelect={handleSuggestion}
              activeSlug={activeStrain}
            />
          </div>
        )}
      </div>
    </div>

    {/* ── Workspace ────────────────────────────────────────── */}
    <main className="min-h-0 flex-1">
      {view === "graph" && (
        <>
        <div className="flex h-full flex-col md:flex-row">
          {/* ── Graph pane (~70%) ─────────────────────────── */}
          <section className="relative min-h-0 min-w-0 flex-1">
            {neighborhoodLoading && !neighborhood && !researching && (
              <CanvasSkeleton strain={activeStrain} />
            )}

            {researching && !neighborhood && (
              <PaneNotice
                title={`Searching the open web for ${activeStrain}…`}
                subtitle="A claim is kept only when a source states it. They appear here as they're found."
              />
            )}

            {/* Error state — no graph */}
            {neighborhoodError && !neighborhood && !researching && !researchClaims && (
              <EmptyState
                strain={activeStrain}
                onRetry={handleRetry}
                onResearch={handleResearchClick}
                suggestions={notFoundSuggestions}
                onSuggestion={handleNotfoundSuggestion}
              />
            )}

            {neighborhood && (
              <GraphCanvas
                response={neighborhood}
                selectedId={selectedId}
                onSelect={handleSelectNode}
                status={neighborhoodLoading && neighborhood ? "Refreshing…" : null}
              />
            )}
          </section>

          {showRailPeek && (
            <RailPeek
              name={peekName}
              tier={peekTier}
              researching={researching}
              onOpen={() => setRailSheetOpen(true)}
            />
          )}

          {mountDesktopRail && (
            <aside className="hidden h-full w-[340px] shrink-0 flex-col border-l border-[var(--border)] bg-[#12241B] md:flex xl:w-[400px]">
              {railBody}
            </aside>
          )}
        </div>
        {mountMobileSheet && railBody && (
          <MobileRailSheet onClose={() => setRailSheetOpen(false)}>
            {railBody}
          </MobileRailSheet>
        )}
        </>
      )}

      {view === "report" &&
        (neighborhood ? (
          <ReportView
            response={neighborhood}
            onBack={() => setView("graph")}
            onDataChanged={refreshKb}
          />
        ) : (
          <NoStrainState
            what="research dossier"
            strain={activeStrain}
            loading={neighborhoodLoading || researching}
            onGoToGraph={() => setView("graph")}
          />
        ))}

      {view === "sources" &&
        (neighborhood ? (
          <SourcesView response={neighborhood} />
        ) : (
          <NoStrainState
            what="source trail"
            strain={activeStrain}
            loading={neighborhoodLoading || researching}
            onGoToGraph={() => setView("graph")}
          />
        ))}

      {view === "dashboard" && <DashboardView />}
    </main>

    <MethodologyDrawer
      open={methodologyOpen}
      onClose={() => setMethodologyOpen(false)}
    />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Phone rail — the desktop column is hidden below md. A peek keeps the
// strain name and tier on screen; Details opens the same rail body.
// ---------------------------------------------------------------------------

function useIsNarrow() {
  const [narrow, setNarrow] = useState<boolean | null>(null);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const apply = () => setNarrow(mq.matches);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);
  return narrow;
}

function RailPeek({
  name,
  tier,
  researching,
  onOpen,
}: {
  name: string;
  tier?: TrustTier;
  researching: boolean;
  onOpen: () => void;
}) {
  const color = tier ? getTrustTierColor(tier) : "var(--accent)";
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full shrink-0 items-center justify-between gap-3 border-t border-[var(--border)] bg-[#12241B] px-4 py-3 text-left md:hidden"
    >
      <span className="min-w-0">
        <span className="mb-0.5 flex items-center gap-2">
          {tier && (
            <span
              className="rounded border px-1.5 py-0.5 text-[8px] uppercase tracking-[0.06em]"
              style={{ color, borderColor: `${color}60` }}
            >
              {getTrustTierLabel(tier)}
            </span>
          )}
          {researching && (
            <span className="text-[9px] uppercase tracking-[0.12em] text-[var(--text-muted)]">
              Researching
            </span>
          )}
        </span>
        <span className="block truncate font-serif text-[18px] italic leading-tight text-[var(--text-primary)]">
          {name}
        </span>
      </span>
      <span className="shrink-0 text-[10px] uppercase tracking-[0.12em] text-[var(--accent)]">
        Details
      </span>
    </button>
  );
}

function MobileRailSheet({
  onClose,
  children,
}: {
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div className="md:hidden">
      <button
        type="button"
        aria-label="Close strain details"
        className="fixed inset-0 z-40 bg-black/50"
        onClick={onClose}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Strain details"
        className="fixed inset-x-0 bottom-0 z-40 flex h-[min(85vh,720px)] flex-col overflow-hidden rounded-t-2xl border-t border-[var(--border)] bg-[#12241B] shadow-[0_-16px_48px_rgba(0,0,0,0.5)]"
      >
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--border)] px-4 py-2.5">
          <span className="text-[10px] uppercase tracking-[0.16em] text-[var(--text-faint)]">
            Strain details
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-[var(--border)] px-2.5 py-1 text-[11px] uppercase tracking-[0.08em] text-[var(--text-muted)] hover:text-[var(--text-primary)]"
          >
            Close
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  );
}

// ---------------------------------------------------------------------------
// No-strain empty state for the gated views (Report / Sources). Honest:
// nothing is rendered as if it were data. Loading reuses the researching
// placeholder; once settled without a neighborhood we say exactly that.
// ---------------------------------------------------------------------------

function NoStrainState({
  what,
  strain,
  loading,
  onGoToGraph,
}: {
  what: string;
  strain: string;
  loading: boolean;
  onGoToGraph: () => void;
}) {
  if (loading) {
    return (
      <PaneNotice
        title={`Loading ${strain}…`}
        subtitle={`The ${what} opens as soon as its neighborhood is on file.`}
      />
    );
  }
  return (
    <div
      className="flex h-full w-full flex-col items-center justify-center gap-5 px-8 text-center"
      style={{ background: "var(--bg-deep)" }}
    >
      <svg
        width="88"
        height="88"
        viewBox="0 0 96 96"
        fill="none"
        className="text-[var(--text-faint)]"
        aria-hidden
      >
        <circle
          cx="48" cy="48" r="40"
          stroke="currentColor" strokeOpacity="0.25" strokeDasharray="2 4"
        />
        <circle cx="48" cy="48" r="6" fill="currentColor" opacity="0.4" />
      </svg>
      <div className="max-w-md">
        <div className="mb-2 text-[10px] uppercase tracking-[0.16em] text-[var(--text-faint)]">
          No strain loaded
        </div>
        <h2 className="text-[18px] font-medium leading-relaxed text-[var(--text-primary)]">
          Pick a strain in the Graph view to see its{" "}
          <span className="italic" style={{ color: "var(--accent)" }}>
            {what}
          </span>
          .
        </h2>
      </div>
      <button
        type="button"
        onClick={onGoToGraph}
        className="rounded-lg border border-[var(--accent)] bg-[var(--accent)]/10 px-3.5 py-1.5 text-[11px] uppercase tracking-[0.08em] text-[var(--accent)] transition-colors hover:bg-[var(--accent)]/20"
      >
        Open the Graph view →
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Loading skeleton + researching placeholder
// ---------------------------------------------------------------------------

function CanvasSkeleton({ strain }: { strain: string }) {
  return (
    <div
      className="flex h-full w-full items-center justify-center"
      style={{ background: "var(--bg-deep)" }}
    >
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
        <div className="text-[11px] uppercase tracking-[0.12em] text-[var(--text-muted)]">
          Loading {strain}…
        </div>
      </div>
    </div>
  );
}

function PaneNotice({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="flex h-full w-full items-center justify-center px-8" style={{ background: "var(--bg-deep)" }}>
      <div className="flex max-w-md flex-col items-center gap-3 text-center">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-[var(--border)] border-t-[var(--accent)]" />
        <div className="text-[14px] font-medium text-[var(--text-primary)]">{title}</div>
        <div className="text-[11.5px] leading-relaxed text-[var(--text-muted)]">{subtitle}</div>
      </div>
    </div>
  );
}
