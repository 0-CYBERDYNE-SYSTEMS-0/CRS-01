"""Scalar strain metadata write semantics — fill-only-if-null.

upsert_strain is the only automated writer of strains.summary /
thc_range / strain_type / breeder. Its contract: the first non-null value
wins, later research runs fill gaps but never churn existing metadata.
image_url is not part of that contract: merges ignore it, and a stored
Wikipedia lead image is cleared the next time the KB opens.
(There is deliberately no automated overwrite and no machine path to
replace a stored value.)
"""
import pytest

from src.graph import kb


def _row(slug):
    with kb.connect() as conn:
        return dict(
            conn.execute("SELECT * FROM strains WHERE slug=?", (slug,)).fetchone()
        )


class TestUpsertStrainFillOnly:
    def test_first_non_null_wins(self):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Test A", summary="First summary")
            kb.upsert_strain(conn, "Test A", summary="Second summary")
        assert _row("test-a")["summary"] == "First summary"

    def test_null_then_value_fills(self):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Test B")
            kb.upsert_strain(conn, "Test B", summary="Late summary", breeder="Breeder")
        row = _row("test-b")
        assert row["summary"] == "Late summary"
        assert row["breeder"] == "Breeder"

    def test_value_then_null_keeps_value(self):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Test C", thc_range="20-24%")
            kb.upsert_strain(conn, "Test C")
        assert _row("test-c")["thc_range"] == "20-24%"

    def test_fields_are_independent(self):
        """One run's partial metadata must not block another field's fill."""
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Test D", summary="Kept")
            kb.upsert_strain(
                conn, "Test D", summary="Ignored", strain_type="sativa"
            )
        row = _row("test-d")
        assert row["summary"] == "Kept"
        assert row["strain_type"] == "sativa"

    def test_last_researched_refreshes(self):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Test E", last_researched=1000)
            kb.upsert_strain(conn, "Test E", last_researched=2000)
        assert _row("test-e")["last_researched"] == 2000


class TestMergeDoesNotChurnMetadata:
    def test_second_run_cannot_overwrite(self):
        """End-to-end through the real pipeline entry point: two merges of
        the same subject with different metadata keep the first values."""
        base = {
            "query": "churn subject",
            "run_id": "run-1",
            "started_at": 1724000000,
            "completed_at": 1724000001,
            "providers_used": ["t"],
            "sources_visited": [],
            "lineage_claims": [],
            "strain_meta": {
                "churn-subject": {
                    "summary": "Original summary",
                    "thc_range": "18-22%",
                    "breeder": "Original Breeder",
                }
            },
        }
        kb.merge_research_run(dict(base, run_id="run-1"))

        second = dict(base, run_id="run-2")
        second["strain_meta"] = {
            "churn-subject": {
                "summary": "Replacement summary",
                "thc_range": "30-40%",
            }
        }
        kb.merge_research_run(second)

        row = _row("churn-subject")
        assert row["summary"] == "Original summary"
        assert row["thc_range"] == "18-22%"
        assert row["breeder"] == "Original Breeder"

    def test_second_run_fills_missing_fields(self):
        base = {
            "query": "fill subject",
            "run_id": "fill-run-1",
            "started_at": 1724000000,
            "completed_at": 1724000001,
            "providers_used": ["t"],
            "sources_visited": [],
            "lineage_claims": [],
            "strain_meta": {"fill-subject": {"summary": "Only a summary"}},
        }
        kb.merge_research_run(base)

        second = dict(base, run_id="fill-run-2")
        second["strain_meta"] = {
            "fill-subject": {"breeder": "Late Breeder", "strain_type": "indica"}
        }
        kb.merge_research_run(second)

        row = _row("fill-subject")
        assert row["summary"] == "Only a summary"
        assert row["breeder"] == "Late Breeder"
        assert row["strain_type"] == "indica"

    def test_merge_ignores_search_image(self):
        """A hero image on the research payload must not become strain metadata."""
        kb.merge_research_run({
            "query": "photo subject",
            "run_id": "photo-run-1",
            "started_at": 1724000000,
            "completed_at": 1724000001,
            "providers_used": ["t"],
            "sources_visited": [],
            "lineage_claims": [],
            "strain_meta": {
                "photo-subject": {
                    "summary": "A real summary",
                    "image_url": "https://upload.wikimedia.org/wikipedia/commons/x/xx/Not_a_plant.jpg",
                }
            },
        })
        row = _row("photo-subject")
        assert row["summary"] == "A real summary"
        assert not row["image_url"]

    def test_open_clears_stored_wikipedia_lead_image(self):
        with kb.connect() as conn:
            kb.upsert_strain(
                conn,
                "Haze",
                image_url="https://upload.wikimedia.org/wikipedia/en/0/05/Hazel.jpg",
            )
            kb.upsert_strain(
                conn,
                "Kept Photo",
                image_url="https://example.com/curator.jpg",
            )
        assert not _row("haze")["image_url"]
        assert _row("kept-photo")["image_url"] == "https://example.com/curator.jpg"
