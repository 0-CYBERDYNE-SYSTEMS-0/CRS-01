"""Agreement semantics — the consensus unit and conflict rule (2026-09-16 fix).

The locked 2026-08-19 model says COMMUNITY_CONSENSUS requires 2+ independent
DOMAINS. Before this fix, three leaks diluted that unit:

1. Provider-synthesized answers (``tavily://answer/…``, ``perplexity://…``)
   passed through domain counting with domain "answer" — an LLM synthesis
   counted as a second, independent witness of the very pages it was
   derived from (circular consensus; minted COMMUNITY_CONSENSUS in prod).
2. ``netloc``-only derivation treated ``ca.leafly.com`` and ``leafly.com``
   as different domains — one publisher across subdomains inflated counts.
3. The orchestrator granted claim-time consensus on distinct URLs (not
   domains) and flagged any two differing parent-tuples as a conflict,
   without the KB's equal-or-subset exemption — so run-payload tiers could
   contradict the tiers the KB derives from the same evidence.

graph/agreement.py is now the single implementation both sides import.
These tests pin: registrable-domain derivation, synthesized exclusion,
the subset rule, and claim-time ↔ derived parity.
"""
import json

from src.graph import kb
from src.graph.agreement import (
    any_parent_sets_conflict,
    independent_domains,
    is_synthesized_url,
    parent_sets_conflict,
    registrable_domain,
)
from src.ingestion.research.extractor import LineageClaim
from src.ingestion.research.orchestrator import (
    ResearchOrchestrator,
    annotate_claim_tiers,
)


# ---------------------------------------------------------------------------
# (a) Registrable-domain derivation
# ---------------------------------------------------------------------------

class TestRegistrableDomain:
    def test_www_strips(self):
        assert registrable_domain("https://www.leafly.com/strains/nl") == "leafly.com"

    def test_subdomains_collapse_onto_registrable_domain(self):
        # One publisher across subdomains is one voice, not two.
        assert registrable_domain("https://ca.leafly.com/x") == "leafly.com"
        assert registrable_domain("https://m.leafly.com/x") == "leafly.com"

    def test_port_and_case_ignored(self):
        assert registrable_domain("http://EXAMPLE.com:8080/a") == "example.com"

    def test_multi_part_suffix_takes_three_labels(self):
        assert registrable_domain("https://www.bbc.co.uk/article") == "bbc.co.uk"
        assert registrable_domain("https://shop.example.com.au/x") == "example.com.au"

    def test_plain_two_label_domain(self):
        assert registrable_domain("https://icmag.com/thread") == "icmag.com"

    def test_hostless_string_keeps_identity(self):
        assert registrable_domain("overgrow-1999-dump") == "overgrow-1999-dump"

    def test_synthesized_urls_have_no_domain(self):
        assert registrable_domain("tavily://answer/Northern_Lights") is None
        assert registrable_domain("perplexity://answer/Northern_Lights") is None


class TestIndependentDomains:
    def test_synthesized_answers_are_not_voices(self):
        urls = [
            "https://www.leafly.com/strains/blueberry-headband",
            "tavily://answer/Blueberry_Headband",
        ]
        assert independent_domains(urls) == {"leafly.com"}

    def test_same_publisher_two_subdomains_is_one_voice(self):
        assert independent_domains([
            "https://ca.leafly.com/a", "https://leafly.com/b",
        ]) == {"leafly.com"}

    def test_two_publishers_are_two_voices(self):
        assert independent_domains([
            "https://leafly.com/a", "https://www.icmag.com/t",
        ]) == {"leafly.com", "icmag.com"}

    def test_is_synthesized_url(self):
        assert is_synthesized_url("tavily://answer/X")
        assert is_synthesized_url("perplexity://answer/X")
        assert not is_synthesized_url("https://tavily.com/results")


# ---------------------------------------------------------------------------
# (b) Parent-set conflict rule (subset = partial telling, not contradiction)
# ---------------------------------------------------------------------------

class TestParentSetConflict:
    def test_disjoint_sets_conflict(self):
        assert parent_sets_conflict({"a", "b"}, {"a", "c"})

    def test_subset_does_not_conflict(self):
        assert not parent_sets_conflict({"a", "b"}, {"a"})

    def test_equal_sets_do_not_conflict(self):
        assert not parent_sets_conflict({"a", "b"}, {"b", "a"})

    def test_any_parent_sets_conflict_scans_pairs(self):
        # {a} vs {a,b} is a subset; only the third tuple makes it a conflict.
        assert any_parent_sets_conflict([{"a"}, {"a", "b"}, {"a", "c"}])
        assert not any_parent_sets_conflict([{"a"}, {"a", "b"}, {"a", "b", "c"}])
        assert not any_parent_sets_conflict([{"a"}])


# ---------------------------------------------------------------------------
# (c) KB derivation — synthesized answers and subdomains must not mint
#     COMMUNITY_CONSENSUS in recompute()
# ---------------------------------------------------------------------------

def _seed_strains(*names):
    """Materialize strain nodes so observations enter aggregates (the
    unresolved-parent gate holds rows whose parent has no node)."""
    with kb.connect() as conn:
        for name in names:
            kb.upsert_strain(conn, name)
        conn.commit()


def _observe(child, parent, url, *, engine="test-extra"):
    with kb.connect() as conn:
        kb.record_lineage_observation(
            conn, child, parent, url,
            confidence=0.7, source_title="t", engine=engine, excerpt="e",
        )
        kb.recompute(conn)


def _edge(child, parent):
    with kb.connect() as conn:
        row = conn.execute(
            "SELECT agreement, source_count, domain_count, source_domains "
            "FROM lineage_edges WHERE child_slug=? AND parent_slug=?",
            (child, parent),
        ).fetchone()
        assert row is not None, f"no edge {child}←{parent}"
        return row


def _strain_tier(slug):
    with kb.connect() as conn:
        row = conn.execute(
            "SELECT trust_tier FROM strains WHERE slug=?", (slug,)
        ).fetchone()
        assert row is not None, f"no strain {slug}"
        return row["trust_tier"]


class TestKbConsensusUnit:
    def test_answer_plus_real_site_is_not_consensus(self):
        """Regression (production data): blueberry-headband sat at
        COMMUNITY_CONSENSUS on domains ["answer", "leafly.com"] — one real
        page plus one Tavily synthesis. The synthesis is not a voice."""
        _seed_strains("Reg Child", "Parent One")
        _observe("reg-child", "parent-one", "https://www.leafly.com/strains/reg-child")
        _seed_strains("Reg Child", "Parent One")
        _observe("reg-child", "parent-one", "tavily://answer/Reg_Child")
        edge = _edge("reg-child", "parent-one")
        assert edge["agreement"] == "single_source"
        assert edge["source_count"] == 2          # both rows counted…
        assert edge["domain_count"] == 1          # …but only one domain
        assert json.loads(edge["source_domains"]) == ["leafly.com"]
        assert _strain_tier("reg-child") == "ANECDOTAL"

    def test_one_publisher_two_subdomains_is_not_consensus(self):
        _seed_strains("Reg Child2", "Parent X")
        _observe("reg-child2", "parent-x", "https://ca.leafly.com/a")
        _seed_strains("Reg Child2", "Parent X")
        _observe("reg-child2", "parent-x", "https://leafly.com/b")
        edge = _edge("reg-child2", "parent-x")
        assert edge["agreement"] == "single_source"
        assert json.loads(edge["source_domains"]) == ["leafly.com"]
        assert _strain_tier("reg-child2") == "ANECDOTAL"

    def test_two_domains_still_reach_consensus(self):
        _seed_strains("Reg Child3", "Parent Y")
        _observe("reg-child3", "parent-y", "https://leafly.com/a")
        _seed_strains("Reg Child3", "Parent Y")
        _observe("reg-child3", "parent-y", "https://www.icmag.com/b")
        edge = _edge("reg-child3", "parent-y")
        assert edge["agreement"] == "multi_source"
        assert _strain_tier("reg-child3") == "COMMUNITY_CONSENSUS"

    def test_subset_parent_sets_do_not_contradict(self):
        """One URL asserts {A,B}; another asserts {A}. A partial telling of
        the same story is not a contradiction — KB stays out of
        CONTRADICTED (regression: the run-time detector used to disagree)."""
        _seed_strains("Reg Child4", "P A", "P B")
        _observe("reg-child4", "p-a", "https://seedbank.test/one")
        _observe("reg-child4", "p-b", "https://seedbank.test/one")
        _seed_strains("Reg Child4", "P A", "P B")
        _observe("reg-child4", "p-a", "https://forum.test/two")
        # p-a has two independent domains → consensus is legitimate; the
        # assertion under test is that a subset tuple never CONTRADICTS.
        assert _strain_tier("reg-child4") == "COMMUNITY_CONSENSUS"
        assert kb.strain_conflicts("reg-child4") == []


# ---------------------------------------------------------------------------
# (d) Pipeline claim-time tiering — must match the KB for the same evidence
# ---------------------------------------------------------------------------

def _payload(*claims):
    return {"lineage_claims": list(claims), "disagreements": []}


class TestClaimTimeConsensusUnit:
    def test_two_urls_one_domain_stays_anecdotal_at_claim_time(self):
        """Regression: the run payload used to bless 2 distinct URLs as
        COMMUNITY_CONSENSUS while the KB (correctly) derived ANECDOTAL."""
        payload = _payload(
            {"child": "Cookies", "parent_a": "OG", "parent_b": "Durban",
             "source_url": "https://ca.leafly.com/one"},
            {"child": "Cookies", "parent_a": "OG", "parent_b": "Durban",
             "source_url": "https://www.leafly.com/two"},
        )
        annotate_claim_tiers(payload)
        assert {c["tier"] for c in payload["lineage_claims"]} == {"ANECDOTAL"}

    def test_answer_plus_real_site_stays_anecdotal_at_claim_time(self):
        payload = _payload(
            {"child": "Cookies", "parent_a": "OG", "parent_b": "Durban",
             "source_url": "https://leafly.com/one"},
            {"child": "Cookies", "parent_a": "OG", "parent_b": "Durban",
             "source_url": "tavily://answer/Cookies"},
        )
        annotate_claim_tiers(payload)
        assert {c["tier"] for c in payload["lineage_claims"]} == {"ANECDOTAL"}

    def test_two_domains_are_consensus_at_claim_time(self):
        payload = _payload(
            {"child": "Cookies", "parent_a": "OG", "parent_b": "Durban",
             "source_url": "https://leafly.com/one"},
            {"child": "Cookies", "parent_a": "OG", "parent_b": "Durban",
             "source_url": "https://icmag.com/two"},
        )
        annotate_claim_tiers(payload)
        assert {c["tier"] for c in payload["lineage_claims"]} == {
            "COMMUNITY_CONSENSUS"
        }


class TestPipelineConflictRule:
    def _orch(self):
        return ResearchOrchestrator()

    def test_subset_tuples_are_not_a_disagreement(self):
        """Regression: any two differing tuples used to flag CONTRADICTED
        in the ResearchingPanel even when the KB said clean (subset)."""
        claims = [
            LineageClaim("Child", "A", "B", "https://s.test/1", "t1", "test"),
            LineageClaim("Child", "A", "", "https://f.test/2", "t2", "test"),
        ]
        assert self._orch()._find_disagreements(claims) == []

    def test_disjoint_tuples_are_a_disagreement(self):
        claims = [
            LineageClaim("Child", "A", "B", "https://s.test/1", "t1", "test"),
            LineageClaim("Child", "A", "C", "https://f.test/2", "t2", "test"),
        ]
        disagreements = self._orch()._find_disagreements(claims)
        assert len(disagreements) == 1
        assert disagreements[0]["child"] == "child"
        by_parents = {tuple(t["parents"]): t["sources"] for t in disagreements[0]["tuples"]}
        assert by_parents[("A", "B")] == 1
        assert by_parents[("A", "C")] == 1

    def test_disagreement_sources_count_urls_of_that_tuple(self):
        claims = [
            LineageClaim("Child", "A", "B", "https://s.test/1", "t1", "test"),
            LineageClaim("Child", "A", "B", "https://s.test/2", "t1", "test"),
            LineageClaim("Child", "A", "C", "https://f.test/2", "t2", "test"),
        ]
        disagreements = self._orch()._find_disagreements(claims)
        assert len(disagreements) == 1
        by_parents = {tuple(t["parents"]): t["sources"] for t in disagreements[0]["tuples"]}
        assert by_parents[("A", "B")] == 2
        assert by_parents[("A", "C")] == 1


# ---------------------------------------------------------------------------
# (e) Parity — the same evidence must tier identically before and after merge
# ---------------------------------------------------------------------------

class TestClaimTimeMatchesDerived:
    def _parity(self, *claims):
        payload = _payload(*claims)
        disagreements = ResearchOrchestrator()._find_disagreements(
            [LineageClaim(
                c["child"], c["parent_a"], c["parent_b"], c["source_url"],
                "t", "test",
            ) for c in claims]
        )
        payload["disagreements"] = disagreements
        annotate_claim_tiers(payload)
        claim_tiers = {c["tier"] for c in payload["lineage_claims"]}

        names = set()
        for c in claims:
            names.add(c["child"])
            names.update(p for p in (c["parent_a"], c["parent_b"]) if p)
        _seed_strains(*sorted(names))
        for c in claims:
            parents = [p for p in (c["parent_a"], c["parent_b"]) if p]
            for parent in parents:
                _observe(c["child"], parent, c["source_url"])
        derived = _strain_tier(kb.normalize_slug(claims[0]["child"]))
        return claim_tiers, derived

    def test_same_domain_evidence_agrees_on_anecdotal(self):
        claim_tiers, derived = self._parity(
            {"child": "Parity One", "parent_a": "P", "parent_b": "Q",
             "source_url": "https://seedbank.test/a"},
            {"child": "Parity One", "parent_a": "P", "parent_b": "Q",
             "source_url": "https://www.seedbank.test/b"},
        )
        assert claim_tiers == {"ANECDOTAL"} == {derived}

    def test_two_domain_evidence_agrees_on_consensus(self):
        claim_tiers, derived = self._parity(
            {"child": "Parity Two", "parent_a": "P", "parent_b": "Q",
             "source_url": "https://seedbank.test/a"},
            {"child": "Parity Two", "parent_a": "P", "parent_b": "Q",
             "source_url": "https://icmag.com/b"},
        )
        assert claim_tiers == {"COMMUNITY_CONSENSUS"}
        assert derived == "COMMUNITY_CONSENSUS"

    def test_synthesized_evidence_agrees_on_anecdotal(self):
        claim_tiers, derived = self._parity(
            {"child": "Parity Three", "parent_a": "P", "parent_b": "Q",
             "source_url": "https://leafly.com/a"},
            {"child": "Parity Three", "parent_a": "P", "parent_b": "Q",
             "source_url": "tavily://answer/Parity_Three"},
        )
        assert claim_tiers == {"ANECDOTAL"} == {derived}

    def test_disjoint_evidence_agrees_on_contradicted(self):
        claim_tiers, derived = self._parity(
            {"child": "Parity Four", "parent_a": "P", "parent_b": "Q",
             "source_url": "https://seedbank.test/a"},
            {"child": "Parity Four", "parent_a": "P", "parent_b": "R",
             "source_url": "https://icmag.com/b"},
        )
        assert claim_tiers == {"CONTRADICTED"}
        assert derived == "CONTRADICTED"

    def test_subset_evidence_agrees_on_no_conflict(self):
        claim_tiers, derived = self._parity(
            {"child": "Parity Five", "parent_a": "P", "parent_b": "Q",
             "source_url": "https://seedbank.test/a"},
            {"child": "Parity Five", "parent_a": "P", "parent_b": "",
             "source_url": "https://icmag.com/b"},
        )
        assert "CONTRADICTED" not in claim_tiers
        assert derived != "CONTRADICTED"
