"""Ledger + spend accounting for research runs.

SPEC §8.2: every submitted run appends to the JSONL ledger — including runs
that fail mid-flight. LLM token spend is captured per extraction pass and
accumulated onto the run (ledger + research_runs table), even when a
response parses to nothing usable. All offline: providers and the LLM are
fakes; the ledger path is redirected by the conftest scratch fixture.
"""
import json

import pytest

from src.ingestion.research import orchestrator as orch_mod
from src.ingestion.research.llm_extractor import LLMExtraction
from src.ingestion.research.providers import ProviderResult
from src.ingestion.research.orchestrator import ResearchOrchestrator
from src.graph import kb


class FakeProvider:
    name = "fake"

    def search(self, query, limit=8):
        return [
            ProviderResult(
                title=f"{query.title()} (cannabis)",
                url=f"https://example.test/{query.lower().replace(' ', '-')}",
                snippet=f"{query} is P1 × P2, a cannabis strain.",
                source="fake",
            )
        ]


def _ledger_rows(path):
    assert path.exists(), "ledger file was never written"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestLedgerOnFailure:
    def test_failed_run_appends_ledger_row(self, monkeypatch, tmp_path):
        """A run that blows up mid-flight still appends its research_run row,
        with the error attached (SPEC §8.2 — no audit-trace blind spots)."""

        def boom(*args, **kwargs):
            raise RuntimeError("consensus exploded")

        monkeypatch.setattr(orch_mod, "consensus_for", boom)
        ledger = tmp_path / "ingest_ledger.jsonl"
        orch = ResearchOrchestrator(providers=[FakeProvider()], max_depth=0)

        run = orch.run("subject")

        assert run.error and "consensus exploded" in run.error
        summary_rows = [
            r for r in _ledger_rows(ledger) if r.get("kind") == "research_run"
        ]
        assert len(summary_rows) == 1, "exactly one ledger row per run"
        assert summary_rows[0]["run_id"] == run.run_id
        assert summary_rows[0]["error"] == run.error

    def test_completed_run_persisted_exactly_once(self, tmp_path):
        """The failure-path persist must not double-append on success."""
        ledger = tmp_path / "ingest_ledger.jsonl"
        orch = ResearchOrchestrator(providers=[FakeProvider()], max_depth=0)

        run = orch.run("subject")

        assert run.error is None
        summary_rows = [
            r for r in _ledger_rows(ledger) if r.get("kind") == "research_run"
        ]
        assert len(summary_rows) == 1
        assert summary_rows[0]["error"] is None

    def test_unwritable_cache_path_still_persists_failure(
        self, monkeypatch, tmp_path
    ):
        """An unwritable CRS_PROVIDER_CACHE_PATH must not skip the ledger.

        Cache init used to run before the try/finally safety net, so a bad
        env var raised with no research_run row. The cache is disposable.
        """
        blocked = tmp_path / "not-a-dir"
        blocked.write_text("file, not a directory", encoding="utf-8")
        monkeypatch.setenv(
            "CRS_PROVIDER_CACHE_PATH", str(blocked / "cache.db")
        )

        def boom(*args, **kwargs):
            raise RuntimeError("consensus exploded")

        monkeypatch.setattr(orch_mod, "consensus_for", boom)
        ledger = tmp_path / "ingest_ledger.jsonl"
        orch = ResearchOrchestrator(providers=[FakeProvider()], max_depth=0)

        run = orch.run("subject")

        assert run.error and "consensus exploded" in run.error
        summary_rows = [
            r for r in _ledger_rows(ledger) if r.get("kind") == "research_run"
        ]
        assert len(summary_rows) == 1, "exactly one ledger row per run"
        assert summary_rows[0]["run_id"] == run.run_id
        assert summary_rows[0]["error"] == run.error

    def test_failed_run_merges_into_kb_with_error(self, monkeypatch):
        """An errored run's dict still merges: the research_runs row carries
        the error (pre-existing contract) and a zeroed usage block."""

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(orch_mod, "consensus_for", boom)
        orch = ResearchOrchestrator(providers=[FakeProvider()], max_depth=0)
        run = orch.run("subject")
        assert run.error

        kb.merge_research_run(run.to_dict())
        row = kb.get_research_run(run.run_id)
        assert row is not None
        assert row["error"] == run.error
        assert row["llm_usage"]["total_tokens"] == 0


class TestLLMUsageAccounting:
    @staticmethod
    def _fake_llm_out(used: bool) -> LLMExtraction:
        return LLMExtraction(
            lineage=(
                [
                    {
                        "child": "subject",
                        "parents": ["P1", "P2"],
                        "source_url": "https://example.test/subject",
                        "source_title": "Subject (cannabis)",
                        "source_engine": "fake",
                        "snippet_excerpt": "Subject is P1 × P2",
                        "confidence": 0.7,
                    }
                ]
                if used
                else []
            ),
            metadata={"summary": "A test subject"} if used else {},
            model="test-model",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            },
        )

    def test_usage_captured_on_successful_run(self, monkeypatch, tmp_path):
        monkeypatch.setattr(orch_mod, "llm_configured", lambda: True)
        monkeypatch.setattr(
            orch_mod, "extract_with_llm", lambda q, r, timeout=45.0: self._fake_llm_out(True)
        )
        ledger = tmp_path / "ingest_ledger.jsonl"
        orch = ResearchOrchestrator(providers=[FakeProvider()], max_depth=0)

        run = orch.run("subject")

        assert run.error is None
        assert run.llm_usage["calls"] == 1
        assert run.llm_usage["model"] == "test-model"
        assert run.llm_usage["prompt_tokens"] == 100
        assert run.llm_usage["completion_tokens"] == 20
        assert run.llm_usage["total_tokens"] == 120
        summary_rows = [
            r for r in _ledger_rows(ledger) if r.get("kind") == "research_run"
        ]
        assert summary_rows[0]["llm_usage"]["total_tokens"] == 120

        kb.merge_research_run(run.to_dict())
        row = kb.get_research_run(run.run_id)
        assert row["llm_usage"] == {
            "calls": 1,
            "model": "test-model",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
        }

    def test_usage_counted_even_when_response_parses_to_nothing(self, monkeypatch):
        """Spend is real even when the response yields zero claims — the
        accounting must not depend on extraction success."""
        monkeypatch.setattr(orch_mod, "llm_configured", lambda: True)
        monkeypatch.setattr(
            orch_mod,
            "extract_with_llm",
            lambda q, r, timeout=45.0: self._fake_llm_out(False),
        )
        orch = ResearchOrchestrator(providers=[FakeProvider()], max_depth=0)

        run = orch.run("subject")

        assert run.error is None
        assert not run.lineage_claims
        assert run.llm_usage["calls"] == 1
        assert run.llm_usage["total_tokens"] == 120
