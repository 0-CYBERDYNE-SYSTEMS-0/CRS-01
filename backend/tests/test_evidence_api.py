"""Tests for the evidence & conflict drill-down endpoints (Work Package A).

These verify that the raw evidence behind every verdict is auditable:
- /graph/strains/{slug}/evidence   — all observations per lineage edge
- /graph/strains/{slug}/conflicts  — derived conflicting parent-sets

Seeding uses the kb.py merge/write functions (never app-external SQL),
except for the quarantine flag which only curation can set — there is no
accessor for it by design, so that one test updates the flag directly and
recomputes, exactly like a curation action would.
"""
from src.graph import kb


def _add_observation(
    child: str,
    parent: str,
    url: str,
    *,
    title: str = "",
    engine: str = "test-extra",
    excerpt: str = "",
    confidence: float = 0.7,
) -> None:
    """Append one raw observation via the kb write path, then re-derive."""
    with kb.connect() as conn:
        kb.record_lineage_observation(
            conn, child, parent, url,
            confidence=confidence, source_title=title, engine=engine,
            excerpt=excerpt,
        )
        kb.recompute(conn)


# ---------------------------------------------------------------------------
# (a) Evidence endpoint: all observations, grouped by parent
# ---------------------------------------------------------------------------

def test_evidence_returns_all_observations_grouped_by_parent(client):
    r = client.get("/api/v1/graph/strains/sherlock/evidence")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "sherlock"
    assert body["child_name"] == "sherlock"

    by_parent = {e["parent"]: e for e in body["edges"]}
    assert set(by_parent) == {"lsd", "skva"}

    # Aggregate fields mirror lineage_edges…
    assert by_parent["lsd"]["parent_name"] == "LSD"
    assert by_parent["lsd"]["agreement"] == "single_source"
    assert by_parent["lsd"]["source_count"] == 1
    assert by_parent["lsd"]["source_domains"] == ["overgrow.com"]

    # …plus the raw observations, excerpt included.
    obs = by_parent["lsd"]["observations"]
    assert len(obs) == 1
    assert obs[0]["source_url"] == "https://overgrow.com/sherlock"
    assert obs[0]["source_title"] == "Sherlock lineage"
    assert obs[0]["engine"] == "test-seed"
    assert obs[0]["excerpt"] == "Sherlock = LSD × SKVA"
    assert obs[0]["observed_at"] > 0


def test_evidence_lists_every_observation_not_a_cap(client):
    """A parent asserted by two sources shows BOTH raw observations."""
    r = client.get("/api/v1/graph/strains/lsd/evidence")
    assert r.status_code == 200
    by_parent = {e["parent"]: e for e in r.json()["edges"]}
    obs = by_parent["mazar-i-sharif"]["observations"]
    assert len(obs) == 2
    assert {o["source_url"] for o in obs} == {
        "https://icmag.com/lsd",
        "https://seedbank.test/mazar",
    }
    assert by_parent["mazar-i-sharif"]["source_count"] == 2
    assert by_parent["mazar-i-sharif"]["agreement"] == "multi_source"


def test_evidence_includes_parent_without_strain_row(client):
    """Raw observations create edges to parents the strains table never saw."""
    _add_observation(
        "sherlock", "Ghost OG", "https://forum.test/ghost-og",
        title="Ghost thread", excerpt="Sherlock out of Ghost OG",
    )
    r = client.get("/api/v1/graph/strains/sherlock/evidence")
    assert r.status_code == 200
    by_parent = {e["parent"]: e for e in r.json()["edges"]}
    assert "ghost-og" in by_parent
    # No strains row → slug as display-name fallback.
    assert by_parent["ghost-og"]["parent_name"] == "ghost-og"
    assert by_parent["ghost-og"]["observations"][0]["excerpt"] == (
        "Sherlock out of Ghost OG"
    )


# ---------------------------------------------------------------------------
# (b) Quarantined rows are excluded from evidence
# ---------------------------------------------------------------------------

def test_evidence_excludes_quarantined_rows(client):
    # Two raw observations on file for lsd ← mazar-i-sharif…
    r = client.get("/api/v1/graph/strains/lsd/evidence")
    by_parent = {e["parent"]: e for e in r.json()["edges"]}
    assert len(by_parent["mazar-i-sharif"]["observations"]) == 2

    # …quarantine one (a curation action — no accessor for this by design,
    # raw rows stay append-only)…
    with kb.connect() as conn:
        conn.execute(
            "UPDATE lineage_sources SET quarantined=1 WHERE source_url=?",
            ("https://seedbank.test/mazar",),
        )
        kb.recompute(conn)

    # …and the row disappears from evidence, aggregate included.
    r = client.get("/api/v1/graph/strains/lsd/evidence")
    by_parent = {e["parent"]: e for e in r.json()["edges"]}
    edge = by_parent["mazar-i-sharif"]
    assert [o["source_url"] for o in edge["observations"]] == [
        "https://icmag.com/lsd"
    ]
    assert edge["source_count"] == 1
    assert edge["source_domains"] == ["icmag.com"]


# ---------------------------------------------------------------------------
# (c) Conflicts: clean strain → []
# ---------------------------------------------------------------------------

def test_conflicts_empty_for_clean_strain(client):
    """Both LSD sources assert the SAME parent set → no conflict."""
    r = client.get("/api/v1/graph/strains/lsd/conflicts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "lsd"
    assert body["conflict_count"] == 0
    assert body["conflicts"] == []


def test_conflicts_superset_assertion_is_not_a_conflict(client):
    """One URL asserting a strict superset ({lsd,skva,+nl}) is NOT a
    conflict — same rule as recompute()'s disagreement detection."""
    with kb.connect() as conn:
        for p in ("LSD", "SKVA", "Northern Lights"):
            kb.record_lineage_observation(
                conn, "sherlock", p, "https://seedlibrary.test/sherlock",
                confidence=0.6, source_title="Seed library", engine="test-extra",
                excerpt="Sherlock = LSD × SKVA × NL",
            )
        kb.recompute(conn)

    r = client.get("/api/v1/graph/strains/sherlock/conflicts")
    assert r.status_code == 200
    assert r.json()["conflicts"] == []


# ---------------------------------------------------------------------------
# (d) Conflicts: two sources asserting different parent sets
# ---------------------------------------------------------------------------

def test_conflicts_detected_with_sources_per_tuple(client):
    # A second source URL asserting an overlapping-but-different pair:
    # overgrow says LSD × SKVA, icmag says Afghan × LSD.
    with kb.connect() as conn:
        for p in ("Afghan", "LSD"):
            kb.record_lineage_observation(
                conn, "sherlock", p, "https://icmag.com/sherlock-thread",
                confidence=0.7, source_title="Sherlock thread",
                engine="test-extra", excerpt="Sherlock = Afghan × LSD",
            )
        kb.recompute(conn)
    r = client.get("/api/v1/graph/strains/sherlock/conflicts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "sherlock"
    assert body["conflict_count"] == 1

    conflict = body["conflicts"][0]
    assert conflict["child"] == "sherlock"
    assert "sherlock" in conflict["summary"]
    assert "2 conflicting parent-strain assertions" in conflict["summary"]

    tuples = conflict["tuples"]
    assert len(tuples) == 2
    by_parents = {tuple(sorted(t["parents"])): t for t in tuples}
    assert set(by_parents) == {
        ("afghan", "lsd"),
        ("lsd", "skva"),
    }
    # Display names resolved from the strains table where they exist.
    skva_tuple = by_parents[("lsd", "skva")]
    assert skva_tuple["parent_names"] == ["LSD", "SKVA"]

    # Sources on each side are the ones asserting THAT tuple.
    assert {s["url"] for s in skva_tuple["sources"]} == {
        "https://overgrow.com/sherlock"
    }
    afghan_tuple = by_parents[("afghan", "lsd")]
    assert {s["url"] for s in afghan_tuple["sources"]} == {
        "https://icmag.com/sherlock-thread"
    }
    assert afghan_tuple["sources"][0]["title"] == "Sherlock thread"
    assert afghan_tuple["sources"][0]["engine"] == "test-extra"
    assert afghan_tuple["sources"][0]["observed_at"] > 0

    # The recompute agrees: sherlock is now CONTRADICTED.
    strain = kb.get_strain("sherlock")
    assert strain["data"]["trust_tier"] == "CONTRADICTED"


def test_conflicts_conflicting_subset_counts(client):
    """An overlapping-but-incomparable set ({afghan}) also conflicts with
    {lsd, skva}; conflicts stay grouped as one entry with 2 tuples."""
    _add_observation(
        "sherlock", "Afghan", "https://leafly.test/sherlock",
        title="Leafly page", engine="test-extra", excerpt="Sherlock = Afghan",
    )
    r = client.get("/api/v1/graph/strains/sherlock/conflicts")
    assert r.status_code == 200
    body = r.json()
    assert body["conflict_count"] == 1
    assert len(body["conflicts"][0]["tuples"]) == 2


# ---------------------------------------------------------------------------
# (e) 404 + suggestions for unknown strains
# ---------------------------------------------------------------------------

def test_evidence_404_with_suggestions_for_unknown_strain(client):
    r = client.get("/api/v1/graph/strains/sherlak/evidence")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "not found" in detail["message"].lower()
    assert isinstance(detail["suggestions"], list)
    assert any(s["slug"] == "sherlock" for s in detail["suggestions"])


def test_conflicts_404_with_suggestions_for_unknown_strain(client):
    r = client.get("/api/v1/graph/strains/sherlak/conflicts")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "not found" in detail["message"].lower()
    assert any(s["slug"] == "sherlock" for s in detail["suggestions"])
