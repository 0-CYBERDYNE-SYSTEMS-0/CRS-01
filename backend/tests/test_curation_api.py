"""Tests for Work Package B — curation loop, run history, backend tier authority.

Covers:
- POST /graph/strains/{slug}/review      — the only path to VERIFIED
- POST /graph/observations/quarantine    — flag bad observations out of the math
- GET  /research/runs, /research/runs/{run_id} — run history read path
- orchestrator.claim_tier / annotate_claim_tiers — backend tier authority

Seeded KB (conftest): sherlock ← {LSD, SKVA} from overgrow.com only
→ ANECDOTAL; lsd ← {Mazar-I-Sharif, Skunk #1} from icmag.com AND
seedbank.test → COMMUNITY_CONSENSUS.
"""
import pytest

from src.graph import kb
from src.ingestion.research.orchestrator import claim_tier, annotate_claim_tiers


# ---------------------------------------------------------------------------
# (a) Quarantine flips derived state…
# ---------------------------------------------------------------------------

def test_quarantine_reduces_edge_and_rederives_tier(client):
    """lsd is a 2-domain consensus edge; quarantine the seedbank source (it
    asserted both parents, so both of its observation rows go) → only one
    domain remains and the tier honestly drops to ANECDOTAL."""
    assert kb.get_strain("lsd")["data"]["trust_tier"] == "COMMUNITY_CONSENSUS"

    for parent in ("mazar-i-sharif", "skunk-#1"):
        r = client.post(
            "/api/v1/graph/observations/quarantine",
            json={
                "child_slug": "lsd",
                "parent_slug": parent,
                "source_url": "https://seedbank.test/mazar",
            },
        )
        assert r.status_code == 200, r.text
    body = client.get("/api/v1/graph/strains/lsd").json()["data"]
    assert body["trust_tier"] == "ANECDOTAL"

    # The evidence view no longer shows the quarantined observation.
    ev = client.get("/api/v1/graph/strains/lsd/evidence").json()
    by_parent = {e["parent"]: e for e in ev["edges"]}
    assert [o["source_url"] for o in by_parent["mazar-i-sharif"]["observations"]] == [
        "https://icmag.com/lsd"
    ]
    assert by_parent["mazar-i-sharif"]["source_domains"] == ["icmag.com"]


def test_quarantining_every_observation_rederives_stale_tier(client):
    """Regression: a child whose observations are ALL quarantined disappears
    from recompute()'s edge map, but its strains row keeps a stale verdict
    unless recompute also revisits edgeless children. lsd starts
    COMMUNITY_CONSENSUS (2 domains) — with no surviving evidence it must
    re-derive to ANECDOTAL, not freeze."""
    assert kb.get_strain("lsd")["data"]["trust_tier"] == "COMMUNITY_CONSENSUS"
    for parent in ("mazar-i-sharif", "skunk-#1"):
        for url in ("https://seedbank.test/mazar", "https://icmag.com/lsd"):
            r = client.post(
                "/api/v1/graph/observations/quarantine",
                json={
                    "child_slug": "lsd",
                    "parent_slug": parent,
                    "source_url": url,
                },
            )
            assert r.status_code == 200, r.text
    body = kb.get_strain("lsd")["data"]
    assert body["trust_tier"] == "ANECDOTAL"
    ev = client.get("/api/v1/graph/strains/lsd/evidence").json()
    assert ev["edges"] == []


def test_quarantine_response_reports_edge_state(client):
    """The quarantine response itself carries the updated strain verdict and
    what remains of that edge — one round-trip, no refetch required."""
    r = client.post(
        "/api/v1/graph/observations/quarantine",
        json={
            "child_slug": "lsd",
            "parent_slug": "mazar-i-sharif",
            "source_url": "https://seedbank.test/mazar",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "lsd"
    assert body["quarantined"] is True
    assert body["edge"]["remaining_observations"] == 1
    assert body["edge"]["agreement"] == "single_source"


def test_quarantining_last_observation_of_an_edge_removes_it(client):
    """A single-source edge loses its aggregate row entirely when its only
    observation is quarantined — but the same source's other edges stay."""
    r = client.post(
        "/api/v1/graph/observations/quarantine",
        json={
            "child_slug": "sherlock",
            "parent_slug": "skva",
            "source_url": "https://overgrow.com/sherlock",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["edge"]["remaining_observations"] == 0
    assert body["edge"]["agreement"] is None

    ev = client.get("/api/v1/graph/strains/sherlock/evidence").json()
    parents = {e["parent"] for e in ev["edges"]}
    assert "skva" not in parents
    assert "lsd" in parents  # same source still backs the lsd edge


def test_quarantine_hides_matching_lineage_claims(client):
    """The LINEAGE claim row from the same source is flagged too (append-only
    — flagged, never deleted)."""
    with kb.connect() as conn:
        live = conn.execute(
            "SELECT COUNT(*) AS c FROM claims WHERE child_slug='sherlock' "
            "AND source_url='https://overgrow.com/sherlock' AND NOT quarantined"
        ).fetchone()["c"]
        assert live == 1

    client.post(
        "/api/v1/graph/observations/quarantine",
        json={
            "child_slug": "sherlock",
            "parent_slug": "lsd",
            "source_url": "https://overgrow.com/sherlock",
        },
    )
    with kb.connect() as conn:
        quarantined = conn.execute(
            "SELECT COUNT(*) AS c FROM claims WHERE child_slug='sherlock' "
            "AND source_url='https://overgrow.com/sherlock' AND quarantined"
        ).fetchone()["c"]
    assert quarantined == 1


# ---------------------------------------------------------------------------
# (b) …and un-quarantine restores it
# ---------------------------------------------------------------------------

def test_unquarantine_restores_tier_and_edge(client):
    payload = {
        "child_slug": "lsd",
        "parent_slug": "mazar-i-sharif",
        "source_url": "https://seedbank.test/mazar",
    }
    # Quarantine the seedbank source across both its parents…
    for parent in ("mazar-i-sharif", "skunk-#1"):
        r1 = client.post("/api/v1/graph/observations/quarantine",
                         json={**payload, "parent_slug": parent,
                               "quarantined": True})
        assert r1.status_code == 200
    assert kb.get_strain("lsd")["data"]["trust_tier"] == "ANECDOTAL"

    # …then restore it: the consensus verdict comes back.
    for parent in ("mazar-i-sharif", "skunk-#1"):
        r2 = client.post("/api/v1/graph/observations/quarantine",
                         json={**payload, "parent_slug": parent,
                               "quarantined": False})
        assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["quarantined"] is False
    assert body["trust_tier"] == "COMMUNITY_CONSENSUS"
    assert body["edge"]["remaining_observations"] == 2
    assert body["edge"]["agreement"] == "multi_source"


def test_quarantine_404_for_unknown_observation(client):
    r = client.post(
        "/api/v1/graph/observations/quarantine",
        json={
            "child_slug": "lsd",
            "parent_slug": "northern-lights",
            "source_url": "https://nowhere.test/x",
        },
    )
    assert r.status_code == 404
    assert "no observation row" in r.json()["detail"]


# ---------------------------------------------------------------------------
# (c) Human review — the only path to VERIFIED
# ---------------------------------------------------------------------------

def test_review_sets_verified_and_survives_recompute(client):
    r = client.post(
        "/api/v1/graph/strains/sherlock/review",
        json={"tier": "VERIFIED", "note": "Breeder's own catalog confirms"},
    )
    assert r.status_code == 200, r.text
    node = r.json()
    assert node["data"]["trust_tier"] == "VERIFIED"
    assert node["data"]["origin"] == "curated"
    assert node["data"]["curated_tier"] == "VERIFIED"
    assert node["data"]["curated_note"] == "Breeder's own catalog confirms"
    assert node["data"]["curated_at"] > 0

    # Force a recompute that would otherwise re-derive CONTRADICTED: a new
    # observation asserts an incompatible parent set. Curation must win.
    with kb.connect() as conn:
        kb.record_lineage_observation(
            conn, "sherlock", "Ghost OG", "https://forum.test/ghost",
            confidence=0.6, engine="test-extra",
        )
        kb.recompute(conn)
    assert kb.get_strain("sherlock")["data"]["trust_tier"] == "VERIFIED"
    assert kb.get_strain("sherlock")["data"]["origin"] == "curated"


def test_review_can_downgrade_and_curated_tier_blocks_recompute(client):
    """Any curatable tier (not just VERIFIED) pins the strain across
    recomputes of the underlying evidence."""
    r = client.post(
        "/api/v1/graph/strains/lsd/review", json={"tier": "ANECDOTAL"}
    )
    assert r.status_code == 200
    assert r.json()["data"]["trust_tier"] == "ANECDOTAL"

    # New consensus-level evidence lands → machine would say
    # COMMUNITY_CONSENSUS; the curation sticks.
    with kb.connect() as conn:
        kb.record_lineage_observation(
            conn, "lsd", "Skunk #1", "https://third-domain.test/lsd",
            confidence=0.8, engine="test-extra",
        )
        kb.recompute(conn)
    assert kb.get_strain("lsd")["data"]["trust_tier"] == "ANECDOTAL"


def test_clearing_review_rederives_tier_from_evidence(client):
    client.post("/api/v1/graph/strains/sherlock/review",
                json={"tier": "VERIFIED", "note": "checked"})
    r = client.post(
        "/api/v1/graph/strains/sherlock/review", json={"tier": None}
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["trust_tier"] == "ANECDOTAL"  # sherlock's derived verdict
    assert data["origin"] == "researched"
    assert data["curated_tier"] is None
    assert data["curated_note"] is None
    assert data["curated_at"] is None


def test_clearing_review_on_consensus_strain_restores_consensus(client):
    client.post("/api/v1/graph/strains/lsd/review", json={"tier": "VERIFIED"})
    r = client.post("/api/v1/graph/strains/lsd/review", json={"tier": None})
    assert r.status_code == 200
    assert r.json()["data"]["trust_tier"] == "COMMUNITY_CONSENSUS"


def test_review_rejects_human_contradicted(client):
    r = client.post(
        "/api/v1/graph/strains/sherlock/review",
        json={"tier": "CONTRADICTED"},
    )
    assert r.status_code == 400
    assert "data-derived" in r.json()["detail"]


def test_review_rejects_unknown_tier(client):
    r = client.post(
        "/api/v1/graph/strains/sherlock/review", json={"tier": "GOLD"}
    )
    assert r.status_code == 400
    assert "GOLD" in r.json()["detail"]


def test_review_404_for_unknown_strain(client):
    r = client.post(
        "/api/v1/graph/strains/sherlak/review", json={"tier": "VERIFIED"}
    )
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "not found" in detail["message"].lower()
    assert any(s["slug"] == "sherlock" for s in detail["suggestions"])


def test_curator_can_verify_over_a_machine_contradiction(client):
    """The point of the human loop: a CONTRADICTED machine verdict can be
    overridden to VERIFIED by a curator (and only by a curator)."""
    with kb.connect() as conn:
        kb.record_lineage_observation(
            conn, "sherlock", "Afghan", "https://icmag.com/sherlock-thread",
            confidence=0.7, engine="test-extra",
        )
        kb.recompute(conn)
    assert kb.get_strain("sherlock")["data"]["trust_tier"] == "CONTRADICTED"

    r = client.post(
        "/api/v1/graph/strains/sherlock/review",
        json={"tier": "VERIFIED", "note": "registry document"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["trust_tier"] == "VERIFIED"


# ---------------------------------------------------------------------------
# (d) Run history
# ---------------------------------------------------------------------------

def test_run_history_newest_first_with_parsed_providers(client):
    with kb.connect() as conn:
        kb.record_run(
            conn, "run-old", "white widow",
            started_at=1724001000, completed_at=1724001100,
            providers_used=["wikipedia"], sources_count=3, claims_count=2,
        )
        kb.record_run(
            conn, "run-new", "blue dream",
            started_at=1724002000, completed_at=1724002100,
            providers_used=["tavily", "wikipedia"], sources_count=5,
            claims_count=4,
        )

    r = client.get("/api/v1/research/runs")
    assert r.status_code == 200, r.text
    body = r.json()
    # seeded_kb already wrote "test-seed-run" (started 1724000000).
    assert body["total"] == 3
    ids = [run["id"] for run in body["runs"]]
    assert ids[:2] == ["run-new", "run-old"]
    newest = body["runs"][0]
    assert newest["query"] == "blue dream"
    assert newest["providers_used"] == ["tavily", "wikipedia"]  # parsed JSON
    assert newest["sources_count"] == 5
    assert newest["claims_count"] == 4
    assert newest["error"] is None


def test_run_history_limit_and_offset(client):
    # Base timestamp sits above the seeded run so ordering is deterministic;
    # the seeded "test-seed-run" (1724000000) still counts toward total.
    with kb.connect() as conn:
        for i in range(5):
            kb.record_run(
                conn, f"run-{i}", f"strain-{i}",
                started_at=1724100000 + i, completed_at=1724100000 + i + 10,
                providers_used=["wikipedia"], sources_count=1, claims_count=1,
            )
    r = client.get("/api/v1/research/runs", params={"limit": 2, "offset": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 6
    assert [run["id"] for run in body["runs"]] == ["run-3", "run-2"]


def test_run_history_includes_error_rows(client):
    with kb.connect() as conn:
        kb.record_run(
            conn, "run-failed", "mystery haze",
            started_at=1724009000, completed_at=1724009050,
            providers_used=["tavily"], sources_count=0, claims_count=0,
            error="all providers failed",
        )
    r = client.get("/api/v1/research/runs", params={"limit": 1})
    body = r.json()
    assert body["runs"][0]["id"] == "run-failed"
    assert body["runs"][0]["error"] == "all providers failed"


def test_run_detail_and_404(client):
    with kb.connect() as conn:
        kb.record_run(
            conn, "run-detail", "northern lights",
            started_at=1724005000, completed_at=1724005100,
            providers_used=["perplexity"], sources_count=2, claims_count=1,
        )
    r = client.get("/api/v1/research/runs/run-detail")
    assert r.status_code == 200
    assert r.json()["providers_used"] == ["perplexity"]
    assert r.json()["query"] == "northern lights"

    r = client.get("/api/v1/research/runs/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# (e) Backend tier authority — pure annotation helpers (no DB, no network)
# ---------------------------------------------------------------------------

class TestClaimTierAnnotation:
    def test_two_distinct_sources_is_community_consensus(self):
        payload = {
            "lineage_claims": [
                {"child": "Cookies", "parent_a": "OG Kush", "parent_b": "Durban",
                 "source_url": "https://a.test/one"},
                {"child": "Cookies", "parent_a": "OG Kush", "parent_b": "Durban",
                 "source_url": "https://b.test/two"},
            ],
            "disagreements": [],
        }
        annotate_claim_tiers(payload)
        assert {c["tier"] for c in payload["lineage_claims"]} == {
            "COMMUNITY_CONSENSUS"
        }

    def test_cross_child_tuple_does_not_pool_consensus(self):
        """Regression: the run-level consensus map is keyed by parents alone,
        so two DIFFERENT children sharing a parent pair must not pool their
        counts — each is single-sourced here and stays ANECDOTAL."""
        payload = {
            "lineage_claims": [
                {"child": "Cookies", "parent_a": "OG Kush", "parent_b": "Durban",
                 "source_url": "https://a.test/one"},
                {"child": "Snapbacks", "parent_a": "OG Kush", "parent_b": "Durban",
                 "source_url": "https://b.test/two"},
            ],
            "disagreements": [],
        }
        annotate_claim_tiers(payload)
        assert {c["tier"] for c in payload["lineage_claims"]} == {"ANECDOTAL"}

    def test_same_url_twice_is_not_consensus(self):
        payload = {
            "lineage_claims": [
                {"child": "Cookies", "parent_a": "OG Kush", "parent_b": "Durban",
                 "source_url": "https://a.test/one"},
                {"child": "Cookies", "parent_a": "OG Kush", "parent_b": "Durban",
                 "source_url": "https://a.test/one"},
            ],
            "disagreements": [],
        }
        annotate_claim_tiers(payload)
        assert {c["tier"] for c in payload["lineage_claims"]} == {"ANECDOTAL"}

    def test_disagreeing_child_is_contradicted(self):
        payload = {
            "lineage_claims": [
                {"child": "Cookies", "parent_a": "OG Kush", "parent_b": "Durban"},
                {"child": "Cookies", "parent_a": "OG Kush", "parent_b": "Chemdawg"},
            ],
            "disagreements": [
                {"child": "cookies", "tuples": [], "summary": "conflict"}
            ],
        }
        annotate_claim_tiers(payload)
        assert {c["tier"] for c in payload["lineage_claims"]} == {"CONTRADICTED"}

    def test_single_source_defaults_to_anecdotal(self):
        payload = {
            "lineage_claims": [
                {"child": "Cookies", "parent_a": "OG Kush", "parent_b": "Durban",
                 "source_url": "https://a.test/one"}
            ],
            "disagreements": [],
        }
        annotate_claim_tiers(payload)
        assert payload["lineage_claims"][0]["tier"] == "ANECDOTAL"

    def test_never_verified(self):
        """Even overwhelming corroboration must stop at COMMUNITY_CONSENSUS —
        VERIFIED is human-only."""
        payload = {
            "lineage_claims": [
                {"child": "Cookies", "parent_a": "OG Kush", "parent_b": "Durban",
                 "source_url": f"https://src{i}.test/x"}
                for i in range(5)
            ],
            "disagreements": [],
        }
        annotate_claim_tiers(payload)
        tiers = {c["tier"] for c in payload["lineage_claims"]}
        assert tiers == {"COMMUNITY_CONSENSUS"}
        assert "VERIFIED" not in tiers

    def test_claim_tier_child_match_is_case_insensitive(self):
        assert claim_tier(
            "Cookies", "A", "B",
            [{"child": "COOKIES", "tuples": []}],
            0,
        ) == "CONTRADICTED"
