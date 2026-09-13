"""Research orchestrator — fan out, extract, dedupe, recurse, persist.

Public API:
    ResearchOrchestrator().run(query) → ResearchRun

A ResearchRun carries:
  - lineage_graph: ReactFlow-shaped nodes/edges (Strain, Claim)
  - lineage_claims: structured LineageClaim list (with sources, extra_parents)
  - consensus: per-parent-tuple count of independent sources
  - disagreements: tuples where 2+ sources assert DIFFERENT parents
  - strain_meta: per-strain metadata from LLM (summary, image, type, breeder)
  - stages: hunter/connector/verifier progress summaries
  - sources_visited: every URL hit (clickable from the UI)
  - raw_responses: provider responses captured for chain-of-custody

Extraction is hybrid: LLM (when OPENAI_API_KEY is set) reads the collected
results and returns structured JSON with source_index anchoring; the regex
extractor runs as fallback or complement. No claim is kept without a
verifiable source URL.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..ledger import ledger_path
from ...graph.kb import normalize_slug
from .providers import (
    ProviderResult,
    get_research_providers,
)
from .extractor import (
    LineageClaim,
    extract_lineage,
    consensus as consensus_for,
)
from .llm_extractor import extract_with_llm, llm_configured
from .wikipedia_full import fetch_wikipedia_full
from .names import (
    _looks_like_cannabis,
    canonical_strain_name,
    is_junk_child_name,
    is_junk_parent_name,
)

logger = logging.getLogger(__name__)


# Defaults — overridden by env vars
MAX_DEPTH = int(os.environ.get("CRS_RESEARCH_MAX_DEPTH", "2"))
MAX_NODES = int(os.environ.get("CRS_RESEARCH_MAX_NODES", "30"))
PER_QUERY_LIMIT = int(os.environ.get("CRS_RESEARCH_PER_QUERY_LIMIT", "8"))
# One ledger path, resolved through ingestion.ledger (kept as a module
# constant: tests patch this name to redirect appends + raw dumps).
LEDGER_PATH = ledger_path()


@dataclass
class ResearchRun:
    query: str
    run_id: str
    started_at: int
    completed_at: int = 0
    providers_used: List[str] = field(default_factory=list)
    sources_visited: List[Dict[str, str]] = field(default_factory=list)
    lineage_claims: List[LineageClaim] = field(default_factory=list)
    consensus: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    disagreements: List[Dict[str, Any]] = field(default_factory=list)
    lineage_graph: Dict[str, Any] = field(default_factory=dict)
    strain_meta: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    stages: List[Dict[str, Any]] = field(default_factory=list)
    raw_dir: Optional[str] = None
    error: Optional[str] = None
    # Accumulated LLM spend for the whole run (all recursion steps): token
    # counts from every provider usage block. Counted even when a response
    # fails to parse or the run errors out — spend happened either way.
    llm_usage: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "providers_used": self.providers_used,
            "sources_visited": self.sources_visited,
            "lineage_claims": [c.to_dict() for c in self.lineage_claims],
            "consensus": self.consensus,
            "disagreements": self.disagreements,
            "lineage_graph": self.lineage_graph,
            "strain_meta": self.strain_meta,
            "stages": self.stages,
            "raw_dir": self.raw_dir,
            "error": self.error,
            "llm_usage": self.llm_usage,
        }


# Strong cannabis-only signals. Generic terms like "hybrid", "genetics", or
# "breed" describe any plant (a citrus page says "a hybrid of pomelo and
# orange"), so they can't gate recursed parents. These tokens mean cannabis.
_STRONG_CANNABIS_HINTS = (
    "cannabis", "marijuana", "hemp", "thc", "cbd", "cannabinoid",
    "terpene", "indica", "sativa", "ruderalis", "kush", "haze",
    "strain", "cultivar", "pot strain", "bud", "weed", "cannabies",
)


def _looks_strongly_cannabis(title: str, snippet: str) -> bool:
    haystack = f"{title} {snippet}".lower()
    return any(h in haystack for h in _STRONG_CANNABIS_HINTS)


# ---------------------------------------------------------------------------
# Backend tier authority — the ONLY tier derivation for fresh claims.
#
# The frontend must not re-derive tiers client-side: each lineage claim in
# the run payload carries the tier the KB's own rules imply. Pure functions
# (no DB, no network) so the rule is directly testable.
# ---------------------------------------------------------------------------

def claim_tier(
    child: str,
    parent_a: str,
    parent_b: str,
    disagreements: List[Dict[str, Any]],
    tuple_source_count: int,
) -> str:
    """Tier for one fresh claim, same semantics as the KB's recompute():
    CONTRADICTED when the child has any conflicting parent-set assertion in
    this run, COMMUNITY_CONSENSUS when this child's parent-tuple is backed
    by 2+ distinct source URLs, else ANECDOTAL. VERIFIED is never returned —
    human-only per the locked 2026-08-19 agreement."""
    child_l = (child or "").strip().lower()
    for d in disagreements or []:
        if (d.get("child") or "").strip().lower() == child_l:
            return "CONTRADICTED"
    if tuple_source_count >= 2:
        return "COMMUNITY_CONSENSUS"
    return "ANECDOTAL"


def _tuple_source_counts(claims: List[Any]) -> Dict[Tuple[str, str, str], int]:
    """Distinct source URLs per (child, parent_a, parent_b).

    Deliberately child-scoped: the run-level ``consensus`` map is keyed by
    parents alone (it is an API payload), so two different children sharing
    a parent pair would pool counts there and mint an unearned
    COMMUNITY_CONSENSUS on a single-source claim. Accepts both LineageClaim
    objects and plain claim dicts.
    """
    counts: Dict[Tuple[str, str, str], set] = {}

    def _get(c: Any, field: str) -> str:
        return str(c.get(field, "") if isinstance(c, dict) else getattr(c, field, ""))

    for c in claims or []:
        key = (
            _get(c, "child").strip().lower(),
            _get(c, "parent_a").strip().lower(),
            _get(c, "parent_b").strip().lower(),
        )
        url = _get(c, "source_url").strip()
        if not url:
            continue
        counts.setdefault(key, set()).add(url)
    return {k: len(v) for k, v in counts.items()}


def annotate_claim_tiers(run_payload: Dict[str, Any]) -> None:
    """Stamp a backend-computed ``tier`` onto every entry of a run payload's
    ``lineage_claims`` (in place). Operates on the plain dict shape so it can
    be exercised without a live run."""
    claims = run_payload.get("lineage_claims") or []
    disagreements = run_payload.get("disagreements") or []
    counts = _tuple_source_counts(claims)
    for c in claims:
        key = (
            str(c.get("child", "")).strip().lower(),
            str(c.get("parent_a", "")).strip().lower(),
            str(c.get("parent_b", "")).strip().lower(),
        )
        c["tier"] = claim_tier(
            c.get("child", ""),
            c.get("parent_a", ""),
            c.get("parent_b", ""),
            disagreements,
            counts.get(key, 0),
        )


class ResearchOrchestrator:
    def __init__(
        self,
        providers: Optional[List[Any]] = None,
        max_depth: int = MAX_DEPTH,
        max_nodes: int = MAX_NODES,
        per_query_limit: int = PER_QUERY_LIMIT,
        ledger_path: Optional[Path] = None,
    ) -> None:
        self.providers = providers if providers is not None else get_research_providers()
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.per_query_limit = per_query_limit
        self.ledger_path = ledger_path or LEDGER_PATH
        self._urls_seen: Set[str] = set()
        self._nodes_visited: Set[str] = set()

    @staticmethod
    def _filter_cannabis_results(
        results: List[ProviderResult], is_subject: bool
    ) -> List[ProviderResult]:
        """Drop off-topic hits so querying a parent that shares a name with a
        fruit (e.g. 'Grapefruit') doesn't pull in citrus lineage.

        - Subject query (``is_subject=True``): lenient filter + keep the top
          hit as a fallback so an obscure strain still yields something.
        - Recursed parent (``is_subject=False``): strict cannabis-only filter
          (strong tokens like thc/strain/kush/indica), no fallback — an honest
          empty beats false fruit lineage.
        """
        if is_subject:
            kept: List[ProviderResult] = [
                r
                for r in results
                if _looks_like_cannabis(r.title or "", None, r.snippet or "")
            ]
            if not kept and results:
                kept = [results[0]]
        else:
            kept = [
                r
                for r in results
                if _looks_like_cannabis(r.title or "", None, r.snippet or "")
                and _looks_strongly_cannabis(r.title or "", r.snippet or "")
            ]
        if len(kept) != len(results):
            logger.info(
                "cannabis filter: %.0f/%.0f results kept (subject=%s)",
                len(kept), len(results), is_subject,
            )
        return kept

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def run(self, query: str) -> ResearchRun:
        run = ResearchRun(
            query=query,
            run_id=str(uuid.uuid4()),
            started_at=int(time.time()),
            providers_used=[p.name for p in self.providers],
        )
        persisted = False
        try:
            raw_dir = self.ledger_path.parent / "raw" / run.run_id
            raw_dir.mkdir(parents=True, exist_ok=True)
            run.raw_dir = str(raw_dir)

            self._research(query, depth=0, run=run)

            # Build consensus + disagreement map.
            cons = consensus_for(run.lineage_claims)
            run.consensus = {
                f"{a} × {b}": entry for (a, b), entry in cons.items()
            }
            run.disagreements = self._find_disagreements(run.lineage_claims)
            run.lineage_graph = self._build_graph(query, run.lineage_claims, run.strain_meta)

            # Backend tier authority: stamp each claim with the tier the
            # KB's own rules imply, so the frontend never re-derives tiers
            # client-side. Never VERIFIED (human-only). Counts are
            # child-scoped — see _tuple_source_counts.
            claim_counts = _tuple_source_counts(run.lineage_claims)
            for c in run.lineage_claims:
                key = (
                    (c.child or "").strip().lower(),
                    (c.parent_a or "").strip().lower(),
                    (c.parent_b or "").strip().lower(),
                )
                c.tier = claim_tier(
                    c.child, c.parent_a, c.parent_b,
                    run.disagreements, claim_counts.get(key, 0),
                )

            # Compute stages — honest mapping of what just happened.
            domains = {
                s.get("engine", "unknown")
                for s in run.sources_visited
            }
            run.stages = [
                {
                    "agent": "hunter",
                    "label": "Hunter",
                    "status": "completed",
                    "summary": f"Discovered {len(run.sources_visited)} sources across {len(domains)} engines",
                    "details": run.sources_visited[:5],
                },
                {
                    "agent": "connector",
                    "label": "Connector",
                    "status": "completed",
                    "summary": f"Linked {len(run.lineage_claims)} lineage edges",
                    "details": [
                        f"{c.child} ← {c.parent_a} × {c.parent_b}"
                        for c in run.lineage_claims[:8]
                    ],
                },
                {
                    "agent": "verifier",
                    "label": "Verifier",
                    "status": "completed",
                    "summary": (
                        f"Consensus: {len(run.consensus)} tuples, "
                        f"{len(run.disagreements)} disagreements"
                    ),
                    "details": [],
                },
            ]

            # Persist to JSONL ledger. SPEC §8.2: every submitted run
            # appends — failures included — so the audit trace has no
            # blind spots.
            self._persist(run)
            persisted = True
        except Exception as e:
            logger.exception("ResearchOrchestrator failed")
            run.error = str(e)
        finally:
            run.completed_at = int(time.time())
            if not persisted:
                # The happy path already wrote the row; research that blew
                # up mid-flight still gets one (with its error attached).
                try:
                    self._persist(run)
                except Exception:
                    logger.exception(
                        "Ledger persist failed for run %s", run.run_id
                    )
        return run

    # ------------------------------------------------------------------
    # Claim ingestion: canonicalize names, drop junk children, dedupe
    # ------------------------------------------------------------------
    @staticmethod
    def _ingest_claims(
        run: "ResearchRun",
        query: str,
        claims: List[LineageClaim],
        seen_tuples: Set[Tuple[str, str]],
    ) -> None:
        for c in claims:
            c.child = canonical_strain_name(c.child)
            c.parent_a = canonical_strain_name(c.parent_a)
            c.parent_b = canonical_strain_name(c.parent_b)
            c.extra_parents = [canonical_strain_name(p) for p in c.extra_parents]
            if is_junk_child_name(c.child, query):
                continue
            parents = [c.parent_a, c.parent_b, *c.extra_parents]
            if any(is_junk_parent_name(p) for p in parents):
                continue
            if (c.parent_a.lower(), c.parent_b.lower()) in seen_tuples:
                continue
            seen_tuples.add((c.parent_a.lower(), c.parent_b.lower()))
            run.lineage_claims.append(c)

    # ------------------------------------------------------------------
    # Recursion
    # ------------------------------------------------------------------
    def _research(self, query: str, depth: int, run: ResearchRun) -> None:
        if depth > self.max_depth:
            return
        if len(self._nodes_visited) >= self.max_nodes:
            return

        results: List[ProviderResult] = []
        for provider in self.providers:
            try:
                rs = provider.search(query, limit=self.per_query_limit)
                for r in rs:
                    if r.url and r.url not in self._urls_seen:
                        self._urls_seen.add(r.url)
                        results.append(r)
                        run.sources_visited.append(
                            {"title": r.title, "url": r.url, "engine": r.source}
                        )
            except Exception as e:
                logger.warning(
                    "Provider %s failed for %s: %s", provider.name, query, e
                )

        # Drop off-topic hits (e.g. citrus pages for a parent named 'Grapefruit').
        # Subject query keeps a fallback hit; recursed parents filter strictly.
        results = self._filter_cannabis_results(results, is_subject=depth == 0)

        # Persist raw responses.
        if run.raw_dir:
            for r in results:
                path = Path(run.raw_dir) / f"{uuid.uuid4().hex[:12]}.json"
                try:
                    path.write_text(
                        json.dumps(r.to_dict(), ensure_ascii=False, indent=2)
                    )
                except OSError:
                    pass

        # ── Content gate: only pages whose text is actually about cannabis
        #    reach extraction. The title/snippet filter above still lets
        #    generic pages through via the subject fallback (kept so obscure
        #    strains yield *something*, e.g. images); letting them into the
        #    regex patterns produced parents like "together plants" from a
        #    Royal Society crop-breeding article queried as "GMO". Raw
        #    copies stay persisted above for chain-of-custody. ────────────
        gated = [
            r
            for r in results
            if _looks_strongly_cannabis(r.title or "", r.snippet or "")
        ]
        if len(gated) != len(results):
            logger.info(
                "content gate: %d/%d results cannabis-relevant for %r",
                len(gated), len(results), query,
            )
        results = gated

        # ── Hybrid extraction: LLM first, regex complement ─────────────
        result_dicts = [r.to_dict() for r in results]
        llm_done = False
        llm_images: Dict[str, str] = {}
        seen_tuples: Set[Tuple[str, str]] = set()
        for c in run.lineage_claims:
            seen_tuples.add((c.parent_a.lower(), c.parent_b.lower()))
            for ep in c.extra_parents:
                seen_tuples.add((ep.lower(), ""))

        if llm_configured():
            llm_out = extract_with_llm(query, result_dicts)
            if llm_out is not None:
                # Spend first: every LLM pass costs tokens whether or not it
                # yielded usable rows, so accrue before anything can skip it.
                self._accrue_llm_usage(run, llm_out)
            if llm_out and llm_out.used:
                llm_claims: List[LineageClaim] = []
                for row in llm_out.lineage:
                    parents = [canonical_strain_name(p) for p in row["parents"]]
                    llm_claims.append(
                        LineageClaim(
                            child=canonical_strain_name(row["child"]),
                            parent_a=parents[0] if len(parents) > 0 else "",
                            parent_b=parents[1] if len(parents) > 1 else "",
                            extra_parents=list(parents[2:]),
                            source_url=row["source_url"],
                            source_title=row["source_title"],
                            source_engine=row["source_engine"],
                            snippet_excerpt=row["snippet_excerpt"],
                            confidence=row["confidence"],
                        )
                    )
                self._ingest_claims(run, query, llm_claims, seen_tuples)
                # Collect metadata for the subject strain.
                if llm_out.metadata:
                    slug = normalize_slug(query)
                    run.strain_meta[slug] = {
                        k: v
                        for k, v in llm_out.metadata.items()
                        if k
                        in ("summary", "strain_type", "thc_range", "breeder", "image_url")
                    }
                    if llm_out.metadata.get("image_url"):
                        llm_images[slug] = llm_out.metadata["image_url"]
                llm_done = True
                logger.info(
                    "LLM extractor: %d claims for %r (%s)",
                    len(llm_out.lineage), query, llm_out.model,
                )

        # Regex fallback / complement — still valuable for patterns the
        # LLM missed, and always runs when no key is configured.
        llm_urls = {
            c["source_url"]
            for c in (llm_out.lineage if llm_out and llm_out.used else [])
        } if llm_done else set()

        for r in results:
            if r.url and llm_done and r.url in llm_urls:
                # Don't double-extract from a source the LLM already mined.
                continue
            slug = normalize_slug(query)
            if r.image_url and slug not in llm_images:
                llm_images[slug] = r.image_url
                run.strain_meta.setdefault(slug, {})["image_url"] = r.image_url
            claims = extract_lineage(query, r.to_dict())
            self._ingest_claims(run, query, claims, seen_tuples)

        # Collect Wikipedia thumbnail images for the subject.
        slug = normalize_slug(query)
        for r in results:
            if r.image_url and not llm_images.get(slug):
                run.strain_meta.setdefault(slug, {})["image_url"] = r.image_url
                llm_images[slug] = r.image_url

        # Wikipedia full-article deep dive.
        cannabis_hints = (
            "cannabis", "marijuana", "hemp", "strain", "kush", "haze",
            "indica", "sativa", "hybrid", "thc", "cbd",
        )
        query_norm = query.lower().strip()
        query_tokens = set(query_norm.split())

        def _score(r: ProviderResult) -> int:
            title_l = r.title.lower()
            snippet_l = r.snippet.lower()
            score = 0
            for h in cannabis_hints:
                if h in title_l:
                    score += 3
                elif h in snippet_l:
                    score += 1
            if "(cannabis" in title_l or "(cannabis" in snippet_l:
                score += 25
            if "disambiguation" in title_l or "may refer to" in snippet_l[:200]:
                score -= 30
            for t in query_tokens:
                if t and t in title_l:
                    score += 5
                elif t and t in snippet_l:
                    score += 1
            negatives = (
                "film", "movie", "song", "album", "tv", "drama",
                "cruise ship", "video game", "rapper", "music",
                "spider", "venom", "spider-man",
            )
            for n in negatives:
                if n in title_l or n in snippet_l[:300]:
                    score -= 8
            return score

        candidates = [
            r
            for r in results
            if r.source.startswith("wikipedia") and "wiki/" in r.url
        ]
        candidates.sort(key=_score, reverse=True)

        for r in candidates:
            if _score(r) <= 0:
                continue
            title = r.url.split("/wiki/")[-1].replace("_", " ")
            full_text = fetch_wikipedia_full(title)
            if full_text:
                fake_result: Dict[str, Any] = {
                    "title": r.title,
                    "url": r.url,
                    "snippet": full_text[:8000],
                    "source": "wikipedia-full",
                }
                extra = extract_lineage(query, fake_result)
                self._ingest_claims(run, query, extra, seen_tuples)
                run.sources_visited.append({
                    "title": f"{r.title} (full article)",
                    "url": r.url,
                    "engine": "wikipedia-full",
                })
                break  # one full fetch per query

        # Follow-up targeted lineage query if first pass found zero parents.
        parents_found = {c.parent_a.lower() for c in run.lineage_claims}
        parents_found.update(c.parent_b.lower() for c in run.lineage_claims)
        if not parents_found and depth == 0:
            targeted = f"{query} parents lineage genetics cross"
            for provider in self.providers:
                try:
                    rs = provider.search(targeted, limit=self.per_query_limit)
                    for r in rs:
                        if (
                            r.url
                            and r.url not in self._urls_seen
                            and _looks_strongly_cannabis(r.title or "", r.snippet or "")
                        ):
                            self._urls_seen.add(r.url)
                            run.sources_visited.append({
                                "title": r.title,
                                "url": r.url,
                                "engine": r.source,
                            })
                            claims = extract_lineage(query, r.to_dict())
                            self._ingest_claims(run, query, claims, seen_tuples)
                except Exception:
                    pass

        # Recurse into discovered parents.
        new_parents: Set[str] = set()
        for c in run.lineage_claims:
            for p in (c.parent_a, c.parent_b, *c.extra_parents):
                pl = p.lower()
                if pl and pl not in self._nodes_visited and pl != query.lower():
                    new_parents.add(p)

        for parent in list(new_parents)[: self.max_nodes - len(self._nodes_visited)]:
            if len(self._nodes_visited) >= self.max_nodes:
                break
            self._nodes_visited.add(parent.lower())
            self._research(parent, depth=depth + 1, run=run)

    # ------------------------------------------------------------------
    # LLM spend accounting
    # ------------------------------------------------------------------
    @staticmethod
    def _accrue_llm_usage(run: "ResearchRun", out: Any) -> None:
        """Fold one extraction pass's provider usage block into the run's
        running totals. Runs even when the pass produced nothing usable —
        tokens were spent either way."""
        usage = getattr(out, "usage", None) or {}
        if not isinstance(usage, dict) or not usage:
            return
        agg = run.llm_usage
        agg["calls"] = agg.get("calls", 0) + 1
        model = getattr(out, "model", "")
        if model:
            agg["model"] = model
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, (int, float)):
                agg[key] = agg.get(key, 0) + int(value)

    # ------------------------------------------------------------------
    # Disagreement detection
    # ------------------------------------------------------------------
    def _find_disagreements(
        self, claims: List[LineageClaim]
    ) -> List[Dict[str, Any]]:
        by_child: Dict[str, List[LineageClaim]] = {}
        for c in claims:
            by_child.setdefault(c.child.lower(), []).append(c)

        disagreements: List[Dict[str, Any]] = []
        for child, clist in by_child.items():
            tuples = {frozenset([c.parent_a, c.parent_b, *c.extra_parents]) for c in clist}
            if len(tuples) > 1:
                disagreements.append({
                    "child": child,
                    "tuples": [
                        {"parents": sorted(t), "sources": len(clist)}
                        for t in tuples
                    ],
                    "summary": f"{child} has {len(tuples)} conflicting parent-strain assertions",
                })
        return disagreements

    # ------------------------------------------------------------------
    # Graph construction
    # ------------------------------------------------------------------
    def _build_graph(
        self, query: str, claims: List[LineageClaim], strain_meta: Dict[str, Dict[str, Any]]
    ) -> Dict[str, Any]:
        nodes_by_id: Dict[str, Dict[str, Any]] = {}
        edges_by_id: Dict[str, Dict[str, Any]] = {}

        root_slug = normalize_slug(query)
        root_meta = strain_meta.get(root_slug, {})
        nodes_by_id[root_slug] = {
            "id": root_slug,
            "type": "Strain",
            "data": {
                "name": query,
                "slug": root_slug,
                "is_root": True,
                "summary": root_meta.get("summary"),
                "image_url": root_meta.get("image_url"),
            },
        }

        # Group claims by (child, parent_tuple).
        groups: Dict[Tuple[str, Tuple[str, ...]], List[LineageClaim]] = {}
        for c in claims:
            pt = tuple([p for p in (c.parent_a, c.parent_b, *c.extra_parents) if p])
            if not pt:
                continue
            groups.setdefault((c.child.lower(), pt), []).append(c)

        # Detect disagreements for coloring.
        child_to_tuples: Dict[str, Set[Tuple[str, ...]]] = {}
        for (child, pt), _ in groups.items():
            child_to_tuples.setdefault(child, set()).add(pt)

        for (child, pt), clist in groups.items():
            # Ensure strain nodes exist for child + every parent.
            for name in [child, *pt]:
                nid = normalize_slug(name)
                if nid not in nodes_by_id:
                    meta = strain_meta.get(nid, {})
                    nodes_by_id[nid] = {
                        "id": nid,
                        "type": "Strain",
                        "data": {
                            "name": name,
                            "slug": nid,
                            "summary": meta.get("summary"),
                            "image_url": meta.get("image_url"),
                        },
                    }

            avg_conf = sum(c.confidence for c in clist) / len(clist)
            unique_urls = list({c.source_url for c in clist if c.source_url})

            # Coloring.
            siblings = child_to_tuples.get(child, set())
            if len(siblings) > 1:
                color = "red"
                agreement = "disagreement"
            elif len(clist) >= 2:
                color = "green"
                agreement = "multi_source"
            else:
                color = "amber"
                agreement = "single_source"

            # Edges: child → each parent.
            for parent_name in pt:
                parent_slug = normalize_slug(parent_name)
                eid = f"e::{normalize_slug(child)}::{parent_slug}"
                edges_by_id[eid] = {
                    "id": eid,
                    "source": normalize_slug(child),
                    "target": parent_slug,
                    "data": {
                        "color": color,
                        "agreement": agreement,
                        "source_count": len(unique_urls),
                        "sources": unique_urls[:5],
                        "avg_confidence": round(avg_conf, 3),
                        "other_parents": [p for p in pt if p != parent_name],
                    },
                    "label": " × ".join(pt) if parent_name == pt[0] else "",
                }

        return {
            "nodes": list(nodes_by_id.values()),
            "edges": list(edges_by_id.values()),
            "node_count": len(nodes_by_id),
            "edge_count": len(edges_by_id),
        }

    # ------------------------------------------------------------------
    # Persistence (JSONL ledger only — KB merge is done by the API route)
    # ------------------------------------------------------------------
    def _persist(self, run: ResearchRun) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as f:
            summary = {
                "kind": "research_run",
                "run_id": run.run_id,
                "query": run.query,
                "started_at": run.started_at,
                "completed_at": run.completed_at,
                "providers_used": run.providers_used,
                "sources_count": len(run.sources_visited),
                "lineage_claims_count": len(run.lineage_claims),
                "disagreement_count": len(run.disagreements),
                "node_count": run.lineage_graph.get("node_count", 0),
                "edge_count": run.lineage_graph.get("edge_count", 0),
                # Honest failure + spend: written for every run, successful
                # or not (see run()'s finally block).
                "error": run.error,
                "llm_usage": run.llm_usage or None,
            }
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
            for c in run.lineage_claims:
                row = {
                    "kind": "lineage_claim",
                    "run_id": run.run_id,
                    "query": run.query,
                    "claim": c.to_dict(),
                    "ingested_at": int(time.time()),
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
