"""Tests for the tester-facing neighborhood endpoint (KB-backed)."""
import pytest
from fastapi.testclient import TestClient


def test_neighborhood_default_depth_2(client):
    """Default depth=2 should return sherlock + parents + grandparents + claims."""
    r = client.get("/api/v1/graph/strains/sherlock/neighborhood")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "sherlock"
    assert body["depth"] == 2
    assert body["center_node_id"] == "sherlock"
    ids = {n["id"] for n in body["nodes"]}
    assert {"sherlock", "lsd", "skva", "mazar-i-sharif", "skunk-#1"}.issubset(ids), f"missing expected nodes: {ids}"
    assert any("silentbreeder" in n["id"] for n in body["nodes"])
    for n in body["nodes"]:
        assert "data" in n
        if n["type"] == "Strain":
            assert "trust_tier" in n["data"]
            assert "confidence" in n["data"]


def test_neighborhood_depth_0_returns_only_seed(client):
    """depth=0 should return just the seed strain."""
    r = client.get("/api/v1/graph/strains/sherlock/neighborhood?depth=0")
    assert r.status_code == 200
    body = r.json()
    assert len(body["nodes"]) == 1
    assert body["nodes"][0]["id"] == "sherlock"
    assert body["edges"] == []


def test_neighborhood_404_on_unknown_strain(client):
    r = client.get("/api/v1/graph/strains/nonexistent-strain-xyz/neighborhood")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert "not found" in (
        detail["message"].lower() if isinstance(detail, dict) else detail.lower()
    )


def test_neighborhood_400_on_bad_depth(client):
    r = client.get("/api/v1/graph/strains/sherlock/neighborhood?depth=99")
    assert r.status_code in (400, 422)


def test_neighborhood_cache_hit_and_etag(client):
    """Second identical request should return X-Cache: HIT and same ETag."""
    r1 = client.get("/api/v1/graph/strains/sherlock/neighborhood?depth=2")
    r2 = client.get("/api/v1/graph/strains/sherlock/neighborhood?depth=2")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.headers.get("X-Cache") in ("MISS", "HIT")
    assert r2.headers.get("X-Cache") == "HIT"
    assert r1.headers.get("ETag") == r2.headers.get("ETag")
    assert r1.headers.get("Cache-Control", "").startswith("private")


def test_neighborhood_304_on_matching_etag(client):
    """Client sending the cached ETag should get a 304."""
    r1 = client.get("/api/v1/graph/strains/sherlock/neighborhood?depth=1")
    etag = r1.headers.get("ETag")
    assert etag
    r2 = client.get(
        "/api/v1/graph/strains/sherlock/neighborhood?depth=1",
        headers={"If-None-Match": etag},
    )
    assert r2.status_code == 304
    assert r2.headers.get("ETag") == etag


def test_neighborhood_purge_cache(client):
    r1 = client.get("/api/v1/graph/strains/sherlock/neighborhood?depth=2")
    assert r1.status_code == 200
    purge = client.post("/api/v1/graph/cache/purge")
    assert purge.status_code == 200
    assert purge.json()["cleared_entries"] >= 1
    r2 = client.get("/api/v1/graph/strains/sherlock/neighborhood?depth=2")
    assert r2.headers.get("X-Cache") == "MISS"


def test_neighborhood_handles_slug_with_dashes_and_spaces(client):
    """Both dashes and spaces in the URL should resolve to the same strain."""
    r1 = client.get("/api/v1/graph/strains/og-kush/neighborhood?depth=0")
    r2 = client.get("/api/v1/graph/strains/OG%20Kush/neighborhood?depth=0")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["center_node_id"] == r2.json()["center_node_id"] == "og-kush"
