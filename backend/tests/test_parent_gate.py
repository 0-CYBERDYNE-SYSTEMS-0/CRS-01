"""Unresolved-parent gate (SPEC §3.1).

An observation naming a parent strain the KB has never seen is recorded
(append-only) but held out of every aggregate, and no strain node is
materialized for the name. The gate clears when the parent becomes a real
KB strain through its own research (deterministic pass at merge time) or a
curator resolves it (POST /graph/parents/{slug}/resolve). Human
quarantine/restore decisions always supersede the mechanical gate.
"""
import pytest

from src.graph import kb


def _merge(child, parents, url, run_id):
    kb.merge_research_run({
        "query": child,
        "run_id": run_id,
        "started_at": 1724000000,
        "completed_at": 1724000001,
        "providers_used": ["t"],
        "sources_visited": [],
        "lineage_claims": [{
            "child": child,
            "parent_a": parents[0],
            "parent_b": parents[1] if len(parents) > 1 else "",
            "extra_parents": list(parents[2:]),
            "source_url": url,
            "source_title": f"{child} thread",
            "source_engine": "t",
            "snippet_excerpt": f"{child} = {' x '.join(parents)}",
            "confidence": 0.7,
            "raw_text": "",
        }],
        "strain_meta": {},
    })


def _observations(child):
    with kb.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM lineage_sources WHERE child_slug=?", (child,)
            ).fetchall()
        ]


def _claims(child):
    with kb.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM claims WHERE child_slug=? AND type='LINEAGE'",
                (child,),
            ).fetchall()
        ]


class TestGateBehavior:
    def test_unknown_parent_is_gated_not_materialized(self):
        _merge("nova", ["Phantom Haze"], "https://src.test/nova", "gate-run-1")

        # No node materialized for the unknown name…
        assert kb.get_strain("phantom-haze") is None
        # …but the observation is on file, flagged…
        obs = _observations("nova")
        assert len(obs) == 1
        assert obs[0]["quarantined"] == 1
        assert obs[0]["quarantine_reason"] == "UNRESOLVED_PARENT"
        # …invisible to the derived graph…
        hood = kb.get_neighborhood("nova", depth=1)
        assert hood["edges"] == []
        assert all(n["id"] != "phantom-haze" for n in hood["nodes"])
        # …and counted honestly in stats.
        assert kb.stats()["pending_parent_observations"] == 1

    def test_known_parent_merges_ungated(self):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Old Timer")
        _merge("nova2", ["Old Timer"], "https://src.test/nova2", "gate-run-2")

        obs = _observations("nova2")
        assert obs[0]["quarantined"] == 0
        assert obs[0]["quarantine_reason"] is None
        hood = kb.get_neighborhood("nova2", depth=1)
        assert any(
            e["target"] == "old-timer" for e in hood["edges"]
        )
        assert kb.stats()["pending_parent_observations"] == 0

    def test_gate_clears_when_parent_materializes_later(self):
        """The deterministic 'hard verifier': once the name becomes a real
        KB strain through its own research, the gated edge comes back."""
        _merge("nova3", ["Phantom Haze"], "https://src.test/nova3", "gate-run-3a")
        assert kb.get_strain("phantom-haze") is None

        # A later run researches the parent itself (it becomes the subject).
        kb.merge_research_run({
            "query": "phantom haze",
            "run_id": "gate-run-3b",
            "started_at": 1724000100,
            "completed_at": 1724000101,
            "providers_used": ["t"],
            "sources_visited": [],
            "lineage_claims": [],
            "strain_meta": {"phantom-haze": {"summary": "Now documented"}},
        })
        assert kb.get_strain("phantom-haze") is not None
        obs = _observations("nova3")[0]
        assert obs["quarantined"] == 0
        assert obs["quarantine_reason"] is None
        hood = kb.get_neighborhood("nova3", depth=1)
        assert any(e["target"] == "phantom-haze" for e in hood["edges"])
        assert kb.stats()["pending_parent_observations"] == 0

    def test_claim_follows_its_gated_observation(self):
        """An assertion is a parent-set: the claim naming a gated parent is
        held back with it, and released when the parent resolves."""
        _merge("nova4", ["Phantom Haze"], "https://src.test/nova4", "gate-run-4")
        claims = _claims("nova4")
        assert len(claims) == 1
        assert claims[0]["quarantined"] == 1
        assert claims[0]["quarantine_reason"] == "UNRESOLVED_PARENT"
        hood = kb.get_neighborhood("nova4", depth=1)
        assert all(n["type"] != "Claim" for n in hood["nodes"])

        kb.resolve_parent("phantom-haze", name="Phantom Haze")
        claims = _claims("nova4")
        assert claims[0]["quarantined"] == 0
        assert claims[0]["quarantine_reason"] is None


class TestCuratorFlow:
    def test_pending_listing_and_resolve_endpoint(self, client):
        _merge("nova5", ["Phantom Haze"], "https://src.test/nova5", "gate-run-5")

        listing = client.get("/api/v1/graph/parents/pending")
        assert listing.status_code == 200, listing.text
        body = listing.json()
        assert body["total_observations"] >= 1
        entry = next(
            p for p in body["parents"] if p["parent_slug"] == "phantom-haze"
        )
        assert entry["children"][0]["child_slug"] == "nova5"
        assert entry["children"][0]["source_url"] == "https://src.test/nova5"

        r = client.post(
            "/api/v1/graph/parents/phantom-haze/resolve",
            json={"name": "Phantom Haze"},
        )
        assert r.status_code == 200, r.text
        resolved = r.json()
        assert resolved["resolved_observations"] == 1
        assert resolved["parent"]["name"] == "Phantom Haze"
        assert resolved["parent"]["origin"] == "resolved"
        assert resolved["children"] == ["nova5"]

        assert kb.get_strain("phantom-haze") is not None
        hood = kb.get_neighborhood("nova5", depth=1)
        assert any(e["target"] == "phantom-haze" for e in hood["edges"])

        after = client.get("/api/v1/graph/parents/pending").json()
        assert all(p["parent_slug"] != "phantom-haze" for p in after["parents"])

    def test_resolve_404_when_nothing_pending(self, client):
        r = client.post(
            "/api/v1/graph/parents/never-heard-of/resolve", json={}
        )
        assert r.status_code == 404
        assert "pending" in r.json()["detail"]

    def test_resolve_without_name_uses_slug_display_name(self, client):
        _merge("nova6", ["Phantom Haze"], "https://src.test/nova6", "gate-run-6")
        r = client.post("/api/v1/graph/parents/phantom-haze/resolve", json={})
        assert r.status_code == 200
        assert r.json()["display_name"] == "Phantom Haze"  # title-cased slug


class TestHumanSupersedesGate:
    def test_human_quarantine_not_re_gated_or_listed(self, client):
        """Quarantining a gated row converts it to a plain human quarantine:
        the gate passes never touch it again, and it leaves the queue."""
        _merge("nova7", ["Phantom Haze"], "https://src.test/nova7", "gate-run-7")
        payload = {
            "child_slug": "nova7",
            "parent_slug": "phantom-haze",
            "source_url": "https://src.test/nova7",
        }
        assert client.post("/api/v1/graph/observations/quarantine",
                           json=payload).status_code == 200

        # A later merge of the same assertion must not re-gate it…
        _merge("nova7", ["Phantom Haze"], "https://src.test/nova7", "gate-run-7b")
        obs = _observations("nova7")[0]
        assert obs["quarantined"] == 1
        assert obs["quarantine_reason"] is None
        # …and it is no longer gate business — nothing pending.
        assert kb.stats()["pending_parent_observations"] == 0

    def test_human_restore_materializes_parent_and_exempts_row(self, client):
        """Restoring a gated observation approves it: the parent node is
        materialized (an un-hidden edge to nothing would dangle) and the row
        is exempt from future gate passes."""
        _merge("nova8", ["Phantom Haze"], "https://src.test/nova8", "gate-run-8")
        payload = {
            "child_slug": "nova8",
            "parent_slug": "phantom-haze",
            "source_url": "https://src.test/nova8",
            "quarantined": False,
        }
        r = client.post("/api/v1/graph/observations/quarantine", json=payload)
        assert r.status_code == 200, r.text

        assert kb.get_strain("phantom-haze") is not None
        obs = _observations("nova8")[0]
        assert obs["quarantined"] == 0
        assert obs["quarantine_reason"] == "HUMAN_APPROVED"

        # A later merge of the same assertion must not re-gate it.
        _merge("nova8", ["Phantom Haze"], "https://src.test/nova8", "gate-run-8b")
        obs = _observations("nova8")[0]
        assert obs["quarantined"] == 0
        assert obs["quarantine_reason"] == "HUMAN_APPROVED"
        assert any(
            e["target"] == "phantom-haze"
            for e in kb.get_neighborhood("nova8", depth=1)["edges"]
        )
