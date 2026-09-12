"""Tests for the deep-research lineage extraction pipeline.

Pure/offline classes (extraction patterns, wikitext parsing) always run.
The live classes (TestLiveWikipedia, TestResearchEndpoint) make real HTTP
requests and only run when CRS_LIVE_TESTS=1 — pytest must stay green with
zero network.
"""
import os

import pytest

from src.ingestion.research.extractor import (
    extract_lineage,
    consensus,
    LineageClaim,
)
from src.ingestion.research.wikipedia_full import (
    fetch_wikipedia_full,
    _strip_wikitext,
    _find_infobox_blocks,
    _flatten_infobox,
)
from src.ingestion.research.providers import ProviderResult
from src.ingestion.research.orchestrator import ResearchOrchestrator

# Gate for every test that touches the live network.
requires_live = pytest.mark.skipif(
    os.environ.get("CRS_LIVE_TESTS") != "1",
    reason="live-network test; set CRS_LIVE_TESTS=1 to enable",
)


# ---------------------------------------------------------------------------
# Pure-extraction unit tests (no network)
# ---------------------------------------------------------------------------

class TestExtractionPatterns:
    def test_x_symbol(self):
        c = extract_lineage(
            "Child",
            {
                "title": "Child",
                "url": "https://x",
                "snippet": "Child is OG Kush × Durban Poison.",
                "source": "leafly",
            },
        )
        assert any(x.parent_a == "OG Kush" and x.parent_b == "Durban Poison" for x in c)

    def test_crossing(self):
        c = extract_lineage(
            "Child",
            {
                "title": "Child",
                "url": "https://x",
                "snippet": "Child was bred by crossing Blueberry and Haze.",
                "source": "leafly",
            },
        )
        assert any(x.parent_a == "Blueberry" and x.parent_b == "Haze" for x in c)

    def test_bred_from(self):
        c = extract_lineage(
            "Child",
            {
                "title": "Child",
                "url": "https://x",
                "snippet": "Child is bred from Afghani and Thai landraces.",
                "source": "wiki",
            },
        )
        assert any(x.parent_a == "Afghani" and x.parent_b == "Thai" for x in c)

    def test_article_body(self):
        # The extractor filters out the child from being its own parent, so
        # we test with White Russian as the child (whose parents are WW and AK-47).
        c = extract_lineage(
            "White Russian",
            {
                "title": "White Russian",
                "url": "https://en.wikipedia.org/wiki/White_Russian_(cannabis)",
                "snippet": "White Russian is an indica-dominant hybrid that is a cross of White Widow and AK-47.",
                "source": "wikipedia",
            },
        )
        pairs = {(x.parent_a, x.parent_b) for x in c}
        found = any(
            (a == "White Widow" and b == "AK-47") or (a == "AK-47" and b == "White Widow")
            for a, b in pairs
        )
        assert found, f"expected WW/AK-47 pair in {pairs}"

    def test_infobox_pattern(self):
        """The Wikipedia infobox `hybrid = X × Y` form must match."""
        text = "name = White Widow\nhybrid = Brazilian Sativa × South Indian Indica\n"
        c = extract_lineage(
            "White Widow",
            {"title": "White Widow", "url": "https://x", "snippet": text, "source": "wikipedia-full"},
        )
        assert any(
            x.parent_a == "Brazilian Sativa" and x.parent_b == "South Indian Indica"
            for x in c
        ), f"got: {[(x.parent_a, x.parent_b) for x in c]}"

    def test_clean_name_strips_articles(self):
        from src.ingestion.research.extractor import _clean_name
        assert _clean_name("a Brazilian Sativa") == "Brazilian Sativa"
        assert _clean_name("an OG Kush") == "OG Kush"
        assert _clean_name("the White Widow") == "White Widow"
        assert _clean_name("is OG Kush") == "OG Kush"

    def test_clean_name_strips_hash_suffix(self):
        from src.ingestion.research.extractor import _clean_name
        assert _clean_name("Gorilla Glue #4") == "Gorilla Glue"

    def test_consensus_groups_same_tuples(self):
        c = [
            LineageClaim("Child", "A", "B", "url1", "t1", "wikipedia"),
            LineageClaim("Child", "A", "B", "url2", "t2", "leafly"),
        ]
        result = consensus(c)
        # Keys are tuples; the orchestrator stringifies them at the boundary.
        key = ("A", "B")
        assert key in result
        assert result[key]["count"] == 2
        assert result[key]["avg_confidence"] > 0


# ---------------------------------------------------------------------------
# Wikipedia parsing unit tests
# ---------------------------------------------------------------------------

class TestWikitextParsing:
    def test_find_infobox_blocks_balanced(self):
        text = (
            "Before text.\n"
            "{{Infobox cultivar\n| name = X\n| hybrid = A ×\n\nB\n}}\n"
            "After text."
        )
        blocks = _find_infobox_blocks(text)
        assert len(blocks) == 1
        s, e = blocks[0]
        assert "{{Infobox" in text[s:e]
        assert "}}" in text[e - 2 : e]

    def test_flatten_infobox_merges_multiline_values(self):
        raw = (
            "{{Infobox cultivar\n"
            "| name = White Widow\n"
            "| hybrid = Brazilian Sativa × \n\n"
            "South Indian Indica\n"
            "| species = Cannabis indica\n"
            "}}"
        )
        flat = _flatten_infobox(raw)
        assert "hybrid = Brazilian Sativa × South Indian Indica" in flat
        assert "species = Cannabis indica" in flat

    def test_strip_wikitext_preserves_infobox(self):
        text = (
            "{{Infobox cultivar\n| name = White Widow\n| hybrid = A × B\n}}\n\n"
            "Body text here."
        )
        out = _strip_wikitext(text)
        assert "hybrid = A × B" in out
        assert "Body text here." in out


# ---------------------------------------------------------------------------
# Live Wikipedia integration tests (require network)
# ---------------------------------------------------------------------------

@pytest.mark.live
@requires_live
class TestLiveWikipedia:
    def test_white_widow_extracts_lineage(self):
        text = fetch_wikipedia_full("White Widow (cannabis)")
        assert text is not None
        assert "hybrid = Brazilian Sativa" in text
        claims = extract_lineage(
            "White Widow",
            {
                "title": "White Widow (cannabis)",
                "url": "https://en.wikipedia.org/wiki/White_Widow_(cannabis)",
                "snippet": text,
                "source": "wikipedia-full",
            },
        )
        assert any(
            c.parent_a == "Brazilian Sativa" and c.parent_b == "South Indian Indica"
            for c in claims
        ), f"claims: {[(c.parent_a, c.parent_b) for c in claims]}"

    def test_ak47_extracts_lineage(self):
        text = fetch_wikipedia_full("AK-47 (cannabis)")
        if text is None:
            pytest.skip("AK-47 page not accessible")
        claims = extract_lineage(
            "AK-47",
            {
                "title": "AK-47",
                "url": "https://en.wikipedia.org/wiki/AK-47_(cannabis)",
                "snippet": text,
                "source": "wikipedia-full",
            },
        )
        assert len(claims) >= 1, f"expected at least 1 claim for AK-47, got 0"

    def test_orchestrator_runs_for_known_strain(self):
        """End-to-end: orchestrator pulls Wikipedia, extracts lineage, builds graph."""
        # Force Wikipedia-only by clearing any Tavily/Perplexity keys in env
        os.environ.pop("TAVILY_API_KEY", None)
        os.environ.pop("PERPLEXITY_API_KEY", None)

        orch = ResearchOrchestrator(max_depth=0, max_nodes=4, per_query_limit=5)
        run = orch.run("White Widow")

        assert run.error is None
        assert len(run.sources_visited) >= 1
        # Must find at least the Brazilian Sativa × South Indian Indica lineage
        assert any(
            c.parent_a == "Brazilian Sativa" and c.parent_b == "South Indian Indica"
            for c in run.lineage_claims
        ), (
            f"lineage_claims: {[(c.parent_a, c.parent_b) for c in run.lineage_claims]}"
        )
        graph = run.lineage_graph
        assert graph["node_count"] >= 3  # White Widow + 2 parents
        assert graph["edge_count"] >= 2  # 2 child->parent edges

    def test_orchestrator_handles_unknown_strain_gracefully(self):
        """For a strain with no Wikipedia coverage, return a valid empty
        run rather than crashing."""
        os.environ.pop("TAVILY_API_KEY", None)
        os.environ.pop("PERPLEXITY_API_KEY", None)

        orch = ResearchOrchestrator(max_depth=0, max_nodes=2)
        run = orch.run("xyzzy-nonexistent-strain-12345")
        assert run.error is None
        assert isinstance(run.lineage_claims, list)
        assert isinstance(run.lineage_graph, dict)


# ---------------------------------------------------------------------------
# Endpoint test (in-process FastAPI)
# ---------------------------------------------------------------------------

@pytest.mark.live
@requires_live
class TestResearchEndpoint:
    def test_submit_returns_run(self):
        from fastapi.testclient import TestClient
        from src.main import app

        client = TestClient(app)
        resp = client.post(
            "/api/v1/research/submit",
            json={"query": "White Widow", "max_depth": 0, "max_nodes": 4},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "run" in body
        run = body["run"]
        assert run["query"] == "White Widow"
        assert "lineage_claims" in run
        assert "lineage_graph" in run
        assert "sources_visited" in run

    def test_by_strain_returns_claims(self):
        from fastapi.testclient import TestClient
        from src.main import app

        client = TestClient(app)
        # First submit a research run so there's data in the ledger.
        client.post(
            "/api/v1/research/submit",
            json={"query": "AK-47", "max_depth": 0, "max_nodes": 4},
        )
        resp = client.get("/api/v1/research/by-strain", params={"slug": "ak-47"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["slug"] == "ak-47"
        assert "claims" in body
        assert isinstance(body["claims"], list)