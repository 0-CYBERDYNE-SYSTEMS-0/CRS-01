"""Disposable provider/wiki cache (issue #6).

Ledger stays append-only; this store is a network skip. Empty/failed
fetches are not stored. Alias identity shares one key. Tests never
touch backend/data/provider_cache.db (conftest points CRS_PROVIDER_CACHE_PATH
at a tmp file).
"""
from src.graph import kb
from src.ingestion.research import provider_cache as pc
from src.ingestion.research.orchestrator import ResearchOrchestrator
from src.ingestion.research.providers import (
    ProviderResult,
    WikipediaProvider,
    cached_search,
)
from src.ingestion.research.wikipedia_full import fetch_wikipedia_full


def _blob():
    return [
        {
            "title": "Blue Dream (cannabis)",
            "url": "https://en.wikipedia.org/wiki/Blue_Dream_(cannabis)",
            "snippet": "Blue Dream is a cannabis hybrid.",
            "source": "wikipedia",
        }
    ]


class TestCacheSemantics:
    def test_hit_skips_fetch(self):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return _blob()

        a = pc.cached_payload(
            provider="wikipedia", kind="search", query="Blue Dream", extra="8",
            fetch=fetch,
        )
        b = pc.cached_payload(
            provider="wikipedia", kind="search", query="Blue Dream", extra="8",
            fetch=fetch,
        )
        assert a == b
        assert calls["n"] == 1
        stats = pc.cache_stats()
        assert stats["hits"] >= 1
        assert stats["misses"] >= 1
        assert stats["stores"] >= 1

    def test_content_hash_matches_payload(self):
        payload = _blob()
        pc.cached_payload(
            provider="wikipedia", kind="search", query="hash-me", extra="1",
            fetch=lambda: payload,
        )
        row = pc.peek("wikipedia", "search", "hash-me", extra="1")
        assert row is not None
        assert row["content_hash"] == pc.content_hash(payload)

    def test_empty_fetch_is_not_stored(self):
        pc.cached_payload(
            provider="wikipedia", kind="search", query="missing", extra="1",
            fetch=lambda: [],
        )
        assert pc.peek("wikipedia", "search", "missing", extra="1") is None

    def test_none_fetch_is_not_stored(self):
        pc.cached_payload(
            provider="wikipedia", kind="full", query="Missing Page",
            fetch=lambda: None,
        )
        assert pc.peek("wikipedia", "full", "Missing Page") is None

    def test_ttl_zero_is_passthrough(self, monkeypatch):
        monkeypatch.setenv("CRS_PROVIDER_CACHE_TTL_SECONDS", "0")
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return _blob()

        pc.cached_payload(
            provider="tavily", kind="search", query="x", extra="1", fetch=fetch,
        )
        pc.cached_payload(
            provider="tavily", kind="search", query="x", extra="1", fetch=fetch,
        )
        assert calls["n"] == 2
        assert pc.peek("tavily", "search", "x", extra="1") is None

    def test_expired_entry_refetches(self):
        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return _blob()

        pc.cached_payload(
            provider="ddg", kind="search", query="expire", extra="1", fetch=fetch,
        )
        assert calls["n"] == 1
        with pc.connect() as conn:
            conn.execute("UPDATE provider_cache SET expires_at=1")
        pc.cached_payload(
            provider="ddg", kind="search", query="expire", extra="1", fetch=fetch,
        )
        assert calls["n"] == 2

    def test_unknown_name_keys_on_normalized_slug(self):
        """No invented strain — unknown queries key on normalize_slug."""
        assert kb.canonical_identity("Totally Unknown 99") == "totally-unknown-99"
        pc.cached_payload(
            provider="wikipedia",
            kind="search",
            query="Totally Unknown 99",
            extra="8",
            fetch=_blob,
        )
        row = pc.peek("wikipedia", "search", "totally-unknown-99", extra="8")
        assert row is not None
        assert row["identity"] == "totally-unknown-99"


class TestAliasIdentity:
    def test_gsc_and_girl_scout_cookies_share_one_key(self):
        with kb.connect() as conn:
            kb.upsert_strain(conn, "Girl Scout Cookies")
        # gsc is a builtin safe alias once the canonical strain exists.
        assert kb.canonical_identity("GSC") == "girl-scout-cookies"
        assert kb.canonical_identity("Girl Scout Cookies") == "girl-scout-cookies"
        assert kb.canonical_display_name("GSC") == "Girl Scout Cookies"

        calls = {"n": 0}

        def fetch():
            calls["n"] += 1
            return _blob()

        pc.cached_payload(
            provider="wikipedia", kind="search", query="Girl Scout Cookies",
            extra="8", fetch=fetch,
        )
        pc.cached_payload(
            provider="wikipedia", kind="search", query="GSC", extra="8",
            fetch=fetch,
        )
        assert calls["n"] == 1


class TestProviderWrap:
    def test_cached_search_decorator_skips_second_call(self):
        class Dummy:
            name = "dummy"

            def __init__(self):
                self.calls = 0

            @cached_search
            def search(self, query, limit=10):
                self.calls += 1
                return [
                    ProviderResult(
                        title=f"{query} (cannabis)",
                        url="https://example.test/x",
                        snippet="a cannabis strain",
                        source="dummy",
                    )
                ]

        p = Dummy()
        a = p.search("Blue Dream", limit=8)
        b = p.search("Blue Dream", limit=8)
        assert a and b
        assert p.calls == 1

    def test_wikipedia_search_skips_http_on_hit(self, monkeypatch):
        calls = {"n": 0}

        def fake(self, client, url, params=None):
            calls["n"] += 1

            class R:
                status_code = 200

                def json(self_inner):
                    if params and params.get("action") == "opensearch":
                        return [
                            "Blue Dream",
                            ["Blue Dream (cannabis)"],
                            ["a cannabis strain"],
                            [
                                "https://en.wikipedia.org/wiki/Blue_Dream_(cannabis)"
                            ],
                        ]
                    return {
                        "title": "Blue Dream (cannabis)",
                        "extract": (
                            "Blue Dream is a cannabis strain, a hybrid of "
                            "Blueberry and Haze."
                        ),
                        "content_urls": {
                            "desktop": {
                                "page": "https://en.wikipedia.org/wiki/Blue_Dream_(cannabis)"
                            }
                        },
                    }

            return R()

        monkeypatch.setattr(WikipediaProvider, "_get_with_retry", fake)
        p = WikipediaProvider()
        first = p.search("Blue Dream", limit=1)
        second = p.search("Blue Dream", limit=1)
        assert first and second
        assert first[0].title == second[0].title
        # opensearch + one summary on the miss; hit skips both.
        assert calls["n"] == 2

    def test_wikipedia_full_caches_live_body(self, monkeypatch):
        calls = {"n": 0}

        def fake_live(title, timeout=10.0):
            calls["n"] += 1
            return "genetics = Blueberry × Haze"

        monkeypatch.setattr(
            "src.ingestion.research.wikipedia_full._fetch_wikipedia_full_live",
            fake_live,
        )
        a = fetch_wikipedia_full("Blue Dream (cannabis)")
        b = fetch_wikipedia_full("Blue Dream (cannabis)")
        assert a == b == "genetics = Blueberry × Haze"
        assert calls["n"] == 1


class TestOrchestratorCacheUsage:
    def test_second_run_records_hits(self):
        class CountingProvider:
            name = "counting"

            def __init__(self):
                self.calls = 0

            @cached_search
            def search(self, query, limit=8):
                self.calls += 1
                return [
                    ProviderResult(
                        title=f"{query} (cannabis)",
                        url=f"https://example.test/{query.lower()}",
                        snippet=f"{query} is P1 × P2, a cannabis strain.",
                        source="counting",
                    )
                ]

        p = CountingProvider()
        r1 = ResearchOrchestrator(providers=[p], max_depth=0).run("subject")
        calls_after_first = p.calls
        assert calls_after_first >= 1
        r2 = ResearchOrchestrator(providers=[p], max_depth=0).run("subject")
        assert p.calls == calls_after_first
        assert r1.cache_usage.get("misses", 0) >= 1
        assert r2.cache_usage.get("hits", 0) >= 1
        assert r1.to_dict()["cache_usage"]["misses"] >= 1
