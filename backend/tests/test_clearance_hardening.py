"""Regression tests for CRS-01 release-clearance follow-ups."""

from src.graph import kb
from src.ingestion.research.extractor import LineageClaim
from src.ingestion.research.orchestrator import ResearchOrchestrator
from src.ingestion.research.names import canonical_strain_name, is_junk_parent_name


def test_ambiguous_family_names_reach_parent_gate_without_rewrite():
    claim = LineageClaim(
        child="Runtz",
        parent_a="Cookies",
        parent_b="OG",
        source_url="https://src.test/runtz",
        source_title="Runtz",
        source_engine="test",
    )
    run = type("Run", (), {"lineage_claims": []})()
    ResearchOrchestrator._ingest_claims(run, "Runtz", [claim], set())

    assert canonical_strain_name("Cookies") == "Cookies"
    assert canonical_strain_name("OG") == "OG"
    assert len(run.lineage_claims) == 1
    assert run.lineage_claims[0].parent_a == "Cookies"
    assert run.lineage_claims[0].parent_b == "OG"


def test_prose_and_unknown_parent_names_are_rejected_before_merge():
    assert is_junk_parent_name("as Blue Dream")
    assert is_junk_parent_name("of Unknown")
    assert is_junk_parent_name("unknown")

    claim = LineageClaim(
        child="Runtz",
        parent_a="as Blue Dream",
        parent_b="Unknown",
        source_url="https://src.test/runtz",
        source_title="Runtz",
        source_engine="test",
    )
    run = type("Run", (), {"lineage_claims": []})()
    ResearchOrchestrator._ingest_claims(run, "Runtz", [claim], set())
    assert run.lineage_claims == []


def test_alias_add_and_remove_in_one_request_preserves_addition():
    with kb.connect() as conn:
        kb.upsert_strain(conn, "Chemdawg")

    node = kb.set_strain_aliases("chemdawg", add=["chem-91"], remove=["chem-d"])

    assert "chem-91" in node["data"]["aliases"]
    assert "chem-d" not in node["data"]["aliases"]
    assert kb.get_strain("chem-91")["id"] == "chemdawg"
