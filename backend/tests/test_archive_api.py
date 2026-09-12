"""Tests for Work Package C — real Wayback integration (source durability).

Covers:
- wayback_availability — fail-open parsing of archive.org's answer (HTTP
  is monkeypatched everywhere; no live network in this suite)
- POST /archive/lookup — hit persists to sources and surfaces through the
  evidence join and the claims join (neighborhood claim nodes)
- failure path → archived:false, no raise, previously observed capture kept
- GET /archive/sources — real DB read, newest fetched first
- POST /archive/wayback — honest 501 (reads only, never submits)
- CRS_WAYBACK_AUTOLOOKUP — off by default (merge path makes no network
  calls); on + stubbed lookup persists hits after a merge, route-wired

Seeded KB (conftest): sherlock ← {LSD, SKVA} via overgrow.com; lsd via
icmag.com + seedbank.test.
"""
from datetime import datetime, timezone

import httpx

from src.graph import kb
from src.config import settings
from src.ingestion.archive import (
    autolookup_run_sources,
    wayback_availability,
    wayback_timestamp_to_epoch,
)


class _FakeResponse:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _stub_json(monkeypatch, payload, status=200):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse(status, payload)

    monkeypatch.setattr("src.ingestion.archive.httpx.get", fake_get)


def _raise_on_get(monkeypatch, exc=None):
    def boom(url, params=None, timeout=None):
        raise exc or httpx.ConnectTimeout("offline")

    monkeypatch.setattr("src.ingestion.archive.httpx.get", boom)


_HIT = {
    "archived_snapshots": {
        "closest": {
            "url": "https://web.archive.org/web/20200101000000/https://overgrow.com/sherlock",
            "timestamp": "20200101000000",
            "status": "200",
        }
    }
}


# ---------------------------------------------------------------------------
# (a) wayback_availability — parsing / fail-open behaviour
# ---------------------------------------------------------------------------

def test_wayback_availability_parses_hit(monkeypatch):
    _stub_json(monkeypatch, _HIT)
    result = wayback_availability("https://overgrow.com/sherlock")
    assert result["archived"] is True
    assert result["wayback_url"].startswith("https://web.archive.org/web/2020")
    assert result["archived_at"] == wayback_timestamp_to_epoch("20200101000000")
    assert result["available_status"] == "200"


def test_wayback_timestamp_to_epoch_handles_shapes():
    expected = int(
        datetime(2020, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    )
    assert wayback_timestamp_to_epoch("20200101000000") == expected
    # Truncated variants are padded, garbage is honestly None.
    assert wayback_timestamp_to_epoch("2020") is not None
    assert wayback_timestamp_to_epoch("") is None
    assert wayback_timestamp_to_epoch(None) is None


def test_wayback_availability_fail_open_on_any_failure(monkeypatch):
    # Non-200 …
    _stub_json(monkeypatch, _HIT, status=503)
    assert wayback_availability("https://x.test/a") == {"archived": False}
    # malformed JSON …
    _stub_json(monkeypatch, ValueError("not json"))
    assert wayback_availability("https://x.test/a") == {"archived": False}
    # empty snapshot object …
    _stub_json(monkeypatch, {"archived_snapshots": {}})
    assert wayback_availability("https://x.test/a") == {"archived": False}
    # and a raised connection error — never an exception to the caller.
    _raise_on_get(monkeypatch)
    assert wayback_availability("https://x.test/a") == {"archived": False}


# ---------------------------------------------------------------------------
# (b) POST /archive/lookup — hit persists and surfaces through the joins
# ---------------------------------------------------------------------------

def test_lookup_hit_persists_and_surfaces_everywhere(client, monkeypatch):
    _stub_json(monkeypatch, _HIT)
    r = client.post(
        "/api/v1/archive/lookup", json={"url": "https://overgrow.com/sherlock"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archived"] is True
    assert body["url"] == "https://overgrow.com/sherlock"
    assert body["wayback_url"].startswith("https://web.archive.org/web/")
    assert body["archived_at"] > 0

    # The hit landed on the sources row (which the seed merge created).
    src = kb.list_sources(limit=100)["sources"]
    row = next(s for s in src if s["url"] == "https://overgrow.com/sherlock")
    assert row["wayback_url"] == body["wayback_url"]
    assert row["archived_at"] == body["archived_at"]
    assert row["title"] == "Sherlock lineage"  # known row keeps its title

    # …and surfaces via the evidence join.
    ev = client.get("/api/v1/graph/strains/sherlock/evidence").json()
    obs = [o for e in ev["edges"] for o in e["observations"]]
    hit_obs = [o for o in obs if o["source_url"] == "https://overgrow.com/sherlock"]
    assert hit_obs and hit_obs[0]["wayback_url"] == body["wayback_url"]
    # Sources never looked up (lsd's two) honestly carry None.
    ev_lsd = client.get("/api/v1/graph/strains/lsd/evidence").json()
    obs_lsd = [o for e in ev_lsd["edges"] for o in e["observations"]]
    assert obs_lsd and all(o["wayback_url"] is None for o in obs_lsd)

    # …and via the claims join (neighborhood claim nodes). Sherlock's claim
    # (sourced by overgrow.com) carries the capture; lsd's claims (other
    # sources, never looked up) carry None.
    nh = client.get(
        "/api/v1/graph/strains/sherlock/neighborhood", params={"depth": 1}
    ).json()
    claims = [n for n in nh["nodes"] if n["type"] == "Claim"]
    sherlock_claims = [
        c for c in claims
        if str(c["data"]["value"]).lower().startswith("sherlock")
    ]
    lsd_claims = [
        c for c in claims if str(c["data"]["value"]).lower().startswith("lsd")
    ]
    assert sherlock_claims and lsd_claims
    assert all(c["data"]["wayback_url"] == body["wayback_url"] for c in sherlock_claims)
    assert all(c["data"]["wayback_url"] is None for c in lsd_claims)


def test_lookup_unknown_url_creates_sources_row_with_blank_title(client, monkeypatch):
    _stub_json(monkeypatch, _HIT)
    r = client.post("/api/v1/archive/lookup", json={"url": "https://new.test/page"})
    assert r.status_code == 200
    row = next(
        s for s in kb.list_sources(limit=100)["sources"]
        if s["url"] == "https://new.test/page"
    )
    assert row["title"] == ""
    assert row["wayback_url"].startswith("https://web.archive.org/")


def test_lookup_miss_is_honest_and_records_no_capture(client, monkeypatch):
    _stub_json(monkeypatch, {"archived_snapshots": {}})
    r = client.post(
        "/api/v1/archive/lookup", json={"url": "https://overgrow.com/sherlock"}
    )
    assert r.status_code == 200
    assert r.json() == {"url": "https://overgrow.com/sherlock", "archived": False}
    row = next(
        s for s in kb.list_sources(limit=100)["sources"]
        if s["url"] == "https://overgrow.com/sherlock"
    )
    assert row["wayback_url"] is None


def test_lookup_failure_path_keeps_previously_observed_capture(client, monkeypatch):
    _stub_json(monkeypatch, _HIT)
    assert client.post(
        "/api/v1/archive/lookup", json={"url": "https://overgrow.com/sherlock"}
    ).json()["archived"] is True

    # Now archive.org dies: the fresh answer is honest, the stored capture
    # observed earlier stays on record.
    _raise_on_get(monkeypatch)
    r = client.post("/api/v1/archive/lookup", json={"url": "https://icmag.com/lsd"})
    assert r.status_code == 200
    assert r.json() == {"url": "https://icmag.com/lsd", "archived": False}
    row = next(
        s for s in kb.list_sources(limit=100)["sources"]
        if s["url"] == "https://overgrow.com/sherlock"
    )
    assert row["wayback_url"] is not None


def test_ingest_alias_matches_lookup(client, monkeypatch):
    _stub_json(monkeypatch, _HIT)
    payload = {"url": "https://overgrow.com/sherlock", "strain_hint": "sherlock"}
    r1 = client.post("/api/v1/archive/ingest", json=payload)
    r2 = client.post("/api/v1/archive/lookup", json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json() == r2.json()


def test_wayback_submit_is_honestly_not_implemented(client):
    r = client.post("/api/v1/archive/wayback", params={"url": "https://x.test"})
    assert r.status_code == 501
    assert "not implemented" in r.json()["detail"].lower()


def test_archive_sources_reads_db_newest_first(client, monkeypatch):
    _stub_json(monkeypatch, _HIT)
    client.post(
        "/api/v1/archive/lookup", json={"url": "https://overgrow.com/sherlock"}
    )

    # A freshly fetched source must sort before the seeded ones.
    with kb.connect() as conn:
        kb.upsert_source(conn, "https://fresh.test/now", "Fresh", "test")
        conn.commit()

    r = client.get("/api/v1/archive/sources", params={"limit": 2, "offset": 0})
    assert r.status_code == 200, r.text
    body = r.json()
    seeded_count = len(kb.list_sources(limit=200)["sources"])
    assert body["total"] == seeded_count  # total is the unpaginated count
    assert len(body["sources"]) == 2
    assert body["sources"][0]["url"] == "https://fresh.test/now"
    assert set(body["sources"][0]) == {
        "url", "title", "engine", "fetched_at", "wayback_url", "archived_at",
    }


# ---------------------------------------------------------------------------
# (c) Auto-lookup on merge — opt-in, default OFF, never fails the merge
# ---------------------------------------------------------------------------

_RUN = {
    "query": "northern-lights",
    "run_id": "autolookup-run",
    "started_at": 1724000000,
    "completed_at": 1724000500,
    "providers_used": ["test"],
    "sources_visited": [
        {"title": "NL lineage", "url": "https://a.test/nl", "engine": "test"},
        {"title": "NL reviews", "url": "https://b.test/nl", "engine": "test"},
    ],
    "lineage_claims": [
        {
            "child": "northern lights", "parent_a": "Afghani", "parent_b": "Thai",
            "extra_parents": [], "source_url": "https://a.test/nl",
            "source_title": "NL lineage", "source_engine": "test",
            "snippet_excerpt": "NL = Afghani × Thai", "confidence": 0.7,
            "raw_text": "",
        }
    ],
    "strain_meta": {},
}


def test_autolookup_off_by_default_no_lookup_calls(monkeypatch):
    """Default flag: the hook is a no-op — no network, no capture written."""
    monkeypatch.setattr(settings, "wayback_autolookup", False)

    called = {"n": 0}

    def spy(url):
        called["n"] += 1
        return {"archived": True, "wayback_url": "https://web.archive.org/x",
                "archived_at": 1577836800}

    monkeypatch.setattr("src.ingestion.archive.wayback_availability", spy)
    assert autolookup_run_sources(_RUN) == 0
    assert called["n"] == 0
    rows = kb.list_sources(limit=100)["sources"]
    assert all(s["wayback_url"] is None for s in rows)


def test_merge_hook_with_flag_on_persists_hits(monkeypatch):
    monkeypatch.setattr(settings, "wayback_autolookup", True)

    def fake_lookup(url):
        if url == "https://a.test/nl":
            return {
                "archived": True,
                "wayback_url": "https://web.archive.org/web/20200101000000/" + url,
                "archived_at": 1577836800,
                "available_status": "200",
            }
        return {"archived": False}  # b.test not archived

    monkeypatch.setattr(
        "src.ingestion.archive.wayback_availability", fake_lookup
    )
    counts = kb.merge_research_run(dict(_RUN))
    assert counts["strains"] >= 3

    assert autolookup_run_sources(_RUN) == 1
    rows = {s["url"]: s for s in kb.list_sources(limit=100)["sources"]}
    assert rows["https://a.test/nl"]["wayback_url"].startswith(
        "https://web.archive.org/web/"
    )
    assert rows["https://a.test/nl"]["archived_at"] == 1577836800
    assert rows["https://b.test/nl"]["wayback_url"] is None


def test_merge_hook_never_fails_the_merge(monkeypatch):
    monkeypatch.setattr(settings, "wayback_autolookup", True)

    def boom(url):
        raise RuntimeError("network is lava")

    monkeypatch.setattr("src.ingestion.archive.wayback_availability", boom)
    # No exception escapes the hook.
    assert autolookup_run_sources(_RUN) == 0


def test_autolookup_respects_limit_and_skips_known_captures(monkeypatch):
    monkeypatch.setattr(settings, "wayback_autolookup", True)
    urls = [{"title": f"s{i}", "url": f"https://s{i}.test/x", "engine": "t"}
            for i in range(15)]
    run = dict(_RUN)
    run["sources_visited"] = urls

    calls: list = []

    def fake_lookup(url):
        calls.append(url)
        return {"archived": False}

    monkeypatch.setattr(
        "src.ingestion.archive.wayback_availability", fake_lookup
    )
    assert autolookup_run_sources(run) == 0
    assert len(calls) == 10  # capped at 10 lookups

    # A URL already carrying a capture is skipped entirely.
    calls.clear()
    kb.set_source_archive("https://s0.test/x", "https://web.archive.org/s0", 1)
    assert autolookup_run_sources(run) == 0
    assert "https://s0.test/x" not in calls


def test_research_submit_merge_hook_wired(client, monkeypatch):
    """Route-level hook: with the flag off nothing is recorded even though
    the merge ran; with it on (lookup stubbed) hits land in the sources
    table — through the real research endpoint."""
    from src.api.routes import research as research_route

    class FakeRun:
        error = None

        def to_dict(self):
            return dict(_RUN)

    monkeypatch.setattr(
        research_route.ResearchOrchestrator, "run", lambda self, q: FakeRun()
    )

    def fake_lookup(url):
        return {
            "archived": True,
            "wayback_url": "https://web.archive.org/web/20200101000000/" + url,
            "archived_at": 1577836800,
        }

    monkeypatch.setattr(
        "src.ingestion.archive.wayback_availability", fake_lookup
    )

    monkeypatch.setattr(settings, "wayback_autolookup", False)
    r = client.post("/api/v1/research/submit", json={"query": "northern-lights"})
    assert r.status_code == 200, r.text
    rows = {s["url"]: s for s in kb.list_sources(limit=100)["sources"]}
    assert "https://a.test/nl" in rows  # the merge itself ran…
    assert rows["https://a.test/nl"]["wayback_url"] is None  # …no capture

    monkeypatch.setattr(settings, "wayback_autolookup", True)
    r = client.post("/api/v1/research/submit", json={"query": "northern-lights"})
    assert r.status_code == 200
    rows = {s["url"]: s for s in kb.list_sources(limit=100)["sources"]}
    assert rows["https://a.test/nl"]["wayback_url"].startswith(
        "https://web.archive.org/web/"
    )
    assert rows["https://b.test/nl"]["wayback_url"] is not None
