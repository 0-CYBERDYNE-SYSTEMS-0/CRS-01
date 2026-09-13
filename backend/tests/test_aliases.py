"""Strain aliases + explicit parent roles (issue #4).

Raw lineage_sources slugs are never rewritten. Merge and recompute()
resolve observed names against slug-or-alias; the curator can alias a
pending parent onto an existing strain instead of materializing it.
Role is stored only from explicit cues and never inferred from order.
recompute() still never assigns VERIFIED.
"""
from src.graph import kb
from src.ingestion.research.extractor import extract_lineage, infer_parent_role


def _merge(child, parents, url, run_id, roles=None, excerpt=None):
    claim = {
        "child": child,
        "parent_a": parents[0],
        "parent_b": parents[1] if len(parents) > 1 else "",
        "extra_parents": list(parents[2:]),
        "source_url": url,
        "source_title": f"{child} thread",
        "source_engine": "t",
        "snippet_excerpt": excerpt or f"{child} = {' x '.join(parents)}",
        "confidence": 0.7,
        "raw_text": "",
    }
    if roles:
        claim["parent_roles"] = roles
    kb.merge_research_run({
        "query": child,
        "run_id": run_id,
        "started_at": 1724000000,
        "completed_at": 1724000001,
        "providers_used": ["t"],
        "sources_visited": [],
        "lineage_claims": [claim],
        "strain_meta": {},
    })


def _obs(child):
    with kb.connect() as conn:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM lineage_sources WHERE child_slug=?",
                (kb.normalize_slug(child),),
            ).fetchall()
        ]


class TestAliasCollapse:
    def test_alias_match_collapses_two_names_onto_one_slug(self):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Girl Scout Cookies")
        kb.set_strain_aliases("girl-scout-cookies", add=["gsc"])

        _merge(
            "GMO Cookies",
            ["GSC", "Chemdawg"],
            "https://src.test/gmo-gsc",
            "alias-run-1",
        )
        kb.resolve_parent("chemdawg", name="Chemdawg")

        assert kb.get_strain("gsc")["data"]["slug"] == "girl-scout-cookies"
        assert kb.get_strain("gsc")["id"] == "girl-scout-cookies"
        # No sibling node for the alias spelling.
        assert kb.get_strain("GSC")["id"] == "girl-scout-cookies"
        with kb.connect() as conn:
            slugs = [
                r["slug"]
                for r in conn.execute("SELECT slug FROM strains").fetchall()
            ]
        assert "gsc" not in slugs
        hood = kb.get_neighborhood("gmo-cookies", depth=1)
        assert any(e["target"] == "girl-scout-cookies" for e in hood["edges"])
        assert not any(e["target"] == "gsc" for e in hood["edges"])

    def test_cookies_does_not_auto_collapse(self):
        """Ambiguous family names stay out of the safe-alias seed."""
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Girl Scout Cookies")
        _merge(
            "Runtz",
            ["Cookies", "Zkittlez"],
            "https://src.test/runtz",
            "alias-run-cookies",
        )
        assert kb.get_strain("cookies") is None
        obs = _obs("runtz")
        cookies = [o for o in obs if o["parent_slug"] == "cookies"]
        assert cookies
        assert cookies[0]["quarantine_reason"] == "UNRESOLVED_PARENT"

    def test_search_hits_alias(self, client):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Girl Scout Cookies")
        # gsc is a safe alias; connect() seeds it onto the existing strain.
        matches = client.get("/api/v1/graph/strains/search?q=gsc").json()["matches"]
        assert any(m["slug"] == "girl-scout-cookies" for m in matches)


class TestAliasAwareResolve:
    def test_safe_alias_attaches_without_curator(self):
        """chem-d is a high-precision builtin; if Chemdawg exists it must
        not spawn a sibling node or sit in the gate."""
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Chemdawg")
        _merge(
            "GMO",
            ["Chem D"],
            "https://src.test/gmo-safe",
            "alias-run-safe",
        )
        assert kb.get_strain("chem-d")["id"] == "chemdawg"
        with kb.connect() as conn:
            slugs = [r["slug"] for r in conn.execute("SELECT slug FROM strains")]
        assert "chem-d" not in slugs
        assert kb.stats()["pending_parent_observations"] == 0
        hood = kb.get_neighborhood("gmo", depth=1)
        assert any(e["target"] == "chemdawg" for e in hood["edges"])

    def test_alias_resolve_does_not_materialize_pending_slug(self, client):
        # chem-91 is NOT a builtin alias, so it stays gated until a curator
        # points it at Chemdawg.
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Chemdawg")
        _merge(
            "GMO",
            ["Chem 91", "Girl Scout Cookies"],
            "https://src.test/gmo-chem91",
            "alias-run-2",
        )
        assert kb.get_strain("chem-91") is None
        assert kb.stats()["pending_parent_observations"] >= 1

        r = client.post(
            "/api/v1/graph/parents/chem-91/resolve",
            json={"alias_of": "chemdawg"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["alias_of"] == "chemdawg"
        assert "chem-91" in body["aliases"]
        assert body["parent"]["slug"] == "chemdawg"
        assert kb.get_strain("chem-91")["id"] == "chemdawg"
        with kb.connect() as conn:
            slugs = [r["slug"] for r in conn.execute("SELECT slug FROM strains")]
        assert "chem-91" not in slugs
        # Raw observation keeps the recorded spelling (append-only).
        obs = _obs("gmo")
        assert any(o["parent_slug"] == "chem-91" for o in obs)
        hood = kb.get_neighborhood("gmo", depth=1)
        assert any(e["target"] == "chemdawg" for e in hood["edges"])
        assert not any(e["target"] == "chem-91" for e in hood["edges"])

    def test_materialize_resolve_still_creates_a_node(self, client):
        _merge(
            "nova",
            ["Phantom Haze"],
            "https://src.test/nova-alias",
            "alias-run-3",
        )
        r = client.post(
            "/api/v1/graph/parents/phantom-haze/resolve",
            json={"name": "Phantom Haze"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["alias_of"] is None
        assert kb.get_strain("phantom-haze") is not None
        assert kb.get_strain("phantom-haze")["data"]["origin"] == "resolved"

    def test_alias_resolve_unknown_target_404s_and_stays_gated(self, client):
        _merge(
            "nova",
            ["Chem 91"],
            "https://src.test/nova-missing-target",
            "alias-run-4",
        )
        r = client.post(
            "/api/v1/graph/parents/chem-91/resolve",
            json={"alias_of": "not-a-strain"},
        )
        assert r.status_code == 404
        obs = _obs("nova")
        assert obs[0]["quarantine_reason"] == "UNRESOLVED_PARENT"
        assert kb.get_strain("chem-91") is None

    def test_cannot_alias_onto_another_strains_slug(self, client):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Chemdawg")
            kb.upsert_strain(conn, "OG Kush")
        _merge(
            "GMO",
            ["Chem 91"],
            "https://src.test/gmo-og-conflict",
            "alias-run-5",
        )
        r = client.post(
            "/api/v1/graph/parents/chem-91/resolve",
            json={"alias_of": "og-kush"},
        )
        assert r.status_code == 200, r.text
        bad = client.post(
            "/api/v1/graph/strains/chemdawg/aliases",
            json={"add": ["og-kush"]},
        )
        assert bad.status_code == 400
        assert "already a strain slug" in bad.json()["detail"]


class TestGateUnchanged:
    def test_unknown_parent_still_gated(self):
        _merge("nova", ["Phantom Haze"], "https://src.test/gate-still", "alias-gate-1")
        assert kb.get_strain("phantom-haze") is None
        obs = _obs("nova")
        assert obs[0]["quarantine_reason"] == "UNRESOLVED_PARENT"
        assert kb.get_neighborhood("nova", depth=1)["edges"] == []

    def test_adding_alias_releases_gated_observations(self, client):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Chemdawg")
        _merge(
            "GMO",
            ["Chem 91"],
            "https://src.test/gmo-add-alias",
            "alias-gate-2",
        )
        assert kb.stats()["pending_parent_observations"] >= 1
        r = client.post(
            "/api/v1/graph/strains/chemdawg/aliases",
            json={"add": ["chem-91"]},
        )
        assert r.status_code == 200, r.text
        assert "chem-91" in r.json()["data"]["aliases"]
        assert kb.stats()["pending_parent_observations"] == 0
        hood = kb.get_neighborhood("gmo", depth=1)
        assert any(e["target"] == "chemdawg" for e in hood["edges"])


class TestExplicitRole:
    def test_role_only_from_explicit_evidence(self):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Chemdawg")
            kb.upsert_strain(conn, "Girl Scout Cookies")
        _merge(
            "GMO",
            ["Chemdawg", "Girl Scout Cookies"],
            "https://src.test/gmo-role",
            "role-run-1",
            roles={"Chemdawg": "male", "Girl Scout Cookies": "female"},
            excerpt=(
                "GMO is Chemdawg (male) × Girl Scout Cookies (female)"
            ),
        )
        hood = kb.get_neighborhood("gmo", depth=1)
        roles = {
            e["target"]: e["data"].get("role")
            for e in hood["edges"]
            if e["type"] == "CHILD_OF"
        }
        assert roles["chemdawg"] == "male"
        assert roles["girl-scout-cookies"] == "female"

    def test_role_not_inferred_from_order(self):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "OG Kush")
            kb.upsert_strain(conn, "Durban Poison")
        _merge(
            "Child",
            ["OG Kush", "Durban Poison"],
            "https://src.test/order-role",
            "role-run-2",
            excerpt="Child is OG Kush × Durban Poison.",
        )
        obs = _obs("child")
        assert all(o["role"] is None for o in obs)
        hood = kb.get_neighborhood("child", depth=1)
        for e in hood["edges"]:
            if e["type"] == "CHILD_OF":
                assert e["data"].get("role") is None

    def test_conflicting_roles_leave_edge_unset(self):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Chemdawg")
            kb.upsert_strain(conn, "Girl Scout Cookies")
        _merge(
            "GMO",
            ["Chemdawg", "Girl Scout Cookies"],
            "https://src.test/gmo-role-a",
            "role-run-3a",
            roles={"Chemdawg": "male"},
        )
        _merge(
            "GMO",
            ["Chemdawg", "Girl Scout Cookies"],
            "https://src.test/gmo-role-b",
            "role-run-3b",
            roles={"Chemdawg": "female"},
        )
        hood = kb.get_neighborhood("gmo", depth=1)
        chem = next(
            e for e in hood["edges"]
            if e["type"] == "CHILD_OF" and e["target"] == "chemdawg"
        )
        assert chem["data"].get("role") is None

    def test_infer_parent_role_requires_explicit_cue(self):
        text = "GMO is a cross of Chemdawg and Girl Scout Cookies."
        assert infer_parent_role("Chemdawg", text) is None
        explicit = "Mother: Chemdawg. Father: Girl Scout Cookies."
        assert infer_parent_role("Chemdawg", explicit) == "female"
        assert infer_parent_role("Girl Scout Cookies", explicit) == "male"
        paren = "Chemdawg (male) × Girl Scout Cookies (female)"
        assert infer_parent_role("Chemdawg", paren) == "male"
        extracted = extract_lineage(
            "GMO",
            {
                "title": "GMO",
                "url": "https://x",
                "snippet": (
                    "GMO is Chemdawg × Girl Scout Cookies. "
                    "Chemdawg is the male parent and Girl Scout Cookies "
                    "is the female parent."
                ),
                "source": "t",
            },
        )
        assert extracted
        roles = extracted[0].parent_roles
        assert roles.get("Chemdawg") == "male"
        assert roles.get("Girl Scout Cookies") == "female"


class TestRecomputeNeverVerified:
    def test_alias_recompute_does_not_assign_verified(self):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Chemdawg")
        _merge(
            "GMO",
            ["Chemdawg"],
            "https://src.test/gmo-verified",
            "role-run-verified",
        )
        kb.set_curated_tier("gmo", "VERIFIED", "breeder note")
        kb.set_strain_aliases("chemdawg", add=["chem-d"])
        assert kb.get_strain("gmo")["data"]["trust_tier"] == "VERIFIED"
        assert kb.get_strain("gmo")["data"]["curated_tier"] == "VERIFIED"
        with kb.connect() as conn:
            kb.recompute(conn)
        assert kb.get_strain("gmo")["data"]["trust_tier"] == "VERIFIED"
