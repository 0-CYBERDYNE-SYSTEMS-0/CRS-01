"""GET /graph/stats spend rollup + provider-cache counters (issue #6).

Token columns already exist on research_runs (#1). This exposes the SUM
and the disposable cache hit rate so backends can be tuned with numbers.
"""
from src.graph import kb
from src.ingestion.research import provider_cache as pc


def test_stats_llm_spend_sums_runs(client):
    with kb.connect() as conn:
        kb.record_run(
            conn,
            "spend-run-a",
            "alpha",
            started_at=1,
            completed_at=2,
            usage={
                "calls": 2,
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
                "model": "test",
            },
        )
        kb.record_run(
            conn,
            "spend-run-b",
            "beta",
            started_at=3,
            completed_at=4,
            usage={
                "calls": 1,
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "total_tokens": 30,
            },
        )
    spend = kb.stats()["llm_spend"]
    assert spend["calls"] == 3
    assert spend["prompt_tokens"] == 120
    assert spend["completion_tokens"] == 50
    assert spend["total_tokens"] == 170


def test_stats_endpoint_includes_spend_and_cache(client):
    pc.cached_payload(
        provider="wikipedia",
        kind="search",
        query="stats-probe",
        extra="1",
        fetch=lambda: [{"title": "x", "url": "https://x", "snippet": "y"}],
    )
    pc.cached_payload(
        provider="wikipedia",
        kind="search",
        query="stats-probe",
        extra="1",
        fetch=lambda: [{"title": "x", "url": "https://x", "snippet": "y"}],
    )
    body = client.get("/api/v1/graph/stats").json()
    assert "llm_spend" in body
    assert set(body["llm_spend"]) >= {
        "calls",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
    }
    cache = body["provider_cache"]
    assert cache["hits"] >= 1
    assert cache["misses"] >= 1
    assert cache["entries"] >= 1
    assert cache["hit_rate"] is not None
    assert cache["hit_rate"] >= 0
