"""Test fixtures for CRS-01 backend."""
import os
import json
import pytest


@pytest.fixture(autouse=True)
def scratch_stores(tmp_path, monkeypatch):
    """Tests must never touch the production KB, ledger, or raw evidence.

    kb.connect() reads CRS_KB_PATH per call; the research orchestrator's
    ledger path is a module constant, so it is patched directly (its
    instance default reads LEDGER_PATH at construction). raw/<run_id>/
    evidence is written under ledger_path.parent, so redirecting the ledger
    also redirects the raw dumps. Opt-in fixtures (seeded_kb) run after this
    one and may re-point CRS_KB_PATH.
    """
    monkeypatch.setenv("CRS_KB_PATH", str(tmp_path / "crs01_scratch.db"))
    from src.ingestion.research import orchestrator as research_orchestrator

    ledger = tmp_path / "ingest_ledger.jsonl"
    monkeypatch.setattr(research_orchestrator, "LEDGER_PATH", ledger)


@pytest.fixture(scope="function")
def seeded_kb(tmp_path, monkeypatch):
    """Create a temporary SQLite KB seeded with known strains.

    Sets CRS_KB_PATH → tmp file, then writes a synthetic research run so
    the KB contains the strains that legacy tests expect (sherlock, lsd,
    skva, etc.). The KB derives trust tiers and agreement from the evidence
    rows — nothing here is mock-only; the same code path the product uses.

    Yields the TestClient wrapped around a fresh FastAPI app import
    (which picks up the KB path from env).
    """
    kb_path = tmp_path / "crs01_test.db"
    monkeypatch.setenv("CRS_KB_PATH", str(kb_path))

    # Write a synthetic-but-real-shaped research run that seeds the KB
    # with the legacy strain set.
    from src.graph import kb

    fake_run = {
        "query": "sherlock",
        "run_id": "test-seed-run",
        "started_at": 1724000000,
        "completed_at": 1724000500,
        "providers_used": ["test-seed"],
        "sources_visited": [
            {"title": "Sherlock lineage", "url": "https://overgrow.com/sherlock", "engine": "test-seed"},
            {"title": "LSD strain info", "url": "https://icmag.com/lsd", "engine": "test-seed"},
            {"title": "Mazar genetics", "url": "https://seedbank.test/mazar", "engine": "test-seed"},
            {"title": "Mazar confirmed", "url": "https://glytest.test/mazar2", "engine": "test-seed"},
        ],
        "lineage_claims": [
            {
                "child": "sherlock", "parent_a": "LSD", "parent_b": "SKVA",
                "extra_parents": [], "source_url": "https://overgrow.com/sherlock",
                "source_title": "Sherlock lineage", "source_engine": "test-seed",
                "snippet_excerpt": "Sherlock = LSD × SKVA", "confidence": 0.78, "raw_text": "",
            },
            {
                "child": "LSD", "parent_a": "Mazar-I-Sharif", "parent_b": "Skunk #1",
                "extra_parents": [], "source_url": "https://icmag.com/lsd",
                "source_title": "LSD strain info", "source_engine": "test-seed",
                "snippet_excerpt": "LSD = Mazar × Skunk", "confidence": 0.82, "raw_text": "",
            },
            {
                "child": "LSD", "parent_a": "Mazar-I-Sharif", "parent_b": "Skunk #1",
                "extra_parents": [], "source_url": "https://seedbank.test/mazar",
                "source_title": "Mazar genetics", "source_engine": "test-seed",
                "snippet_excerpt": "Mazar in LSD confirmed", "confidence": 0.75, "raw_text": "",
            },
        ],
        "strain_meta": {
            "sherlock": {"summary": "Hybrid bred by SilentBreeder", "image_url": "", "thc_range": "22-24%", "strain_type": "hybrid", "breeder": "SilentBreeder"},
            "lsd": {"summary": "Indica strain", "strain_type": "indica", "breeder": ""},
            "mazar-i-sharif": {"summary": "Afghani landrace", "strain_type": "indica", "breeder": ""},
            "skunk-1": {"strain_type": "hybrid", "breeder": ""},
            "skva": {"summary": "Hybrid from Cheese genetics", "strain_type": "hybrid", "breeder": ""},
        },
    }
    kb.merge_research_run(fake_run)

    # The unresolved-parent gate (SPEC §3.1) holds observations naming
    # parents the KB has never seen. Resolve the seeded parents so the KB
    # below has the live-node/live-edge shape the downstream tests were
    # written against — the same flow a curator would perform.
    for slug, name in (
        ("skva", "SKVA"),
        ("mazar-i-sharif", "Mazar-I-Sharif"),
        ("skunk-#1", "Skunk #1"),
    ):
        kb.resolve_parent(slug, name=name)

    # Also ensure SKVA strain exists in KB (just the parent from sherlock's lineage)
    # merge_research_run already upserts SKVA via the lineage_claims parent_b.

    # Additional popular strains for catalog search tests.
    more = {
        "blue-dream": {"name": "Blue Dream", "strain_type": "hybrid", "thc_range": "17-24%", "breeder": ""},
        "og-kush": {"name": "OG Kush", "strain_type": "hybrid", "thc_range": "20-25%", "breeder": ""},
        "girl-scout-cookies": {"name": "Girl Scout Cookies", "strain_type": "hybrid", "thc_range": "18-28%", "breeder": ""},
        "northern-lights": {"name": "Northern Lights", "strain_type": "indica", "thc_range": "16-21%", "breeder": ""},
        "white-widow": {"name": "White Widow", "strain_type": "hybrid", "thc_range": "18-25%", "breeder": ""},
        "jack-herer": {"name": "Jack Herer", "strain_type": "sativa", "thc_range": "18-23%", "breeder": ""},
        "sour-diesel": {"name": "Sour Diesel", "strain_type": "sativa", "thc_range": "20-22%", "breeder": ""},
    }
    for slug, data in more.items():
        with kb.connect() as conn:
            kb.upsert_strain(
                conn,
                data["name"],
                strain_type=data.get("strain_type"),
                thc_range=data.get("thc_range"),
                breeder=data.get("breeder", ""),
            )
            conn.commit()

    from fastapi.testclient import TestClient
    from src.main import app
    return TestClient(app)


@pytest.fixture
def client(seeded_kb):
    """Alias — every test gets a seeded KB automatically."""
    return seeded_kb