"""Tests for the strain catalog endpoints:
- GET /api/v1/graph/strains         (list)
- GET /api/v1/graph/strains/search  (substring autocomplete)

Backed by the seeded KB fixture (conftest.py). No mock data.
"""
from __future__ import annotations


def test_list_strains_returns_paginated_results(client):
    resp = client.get("/api/v1/graph/strains?limit=10")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["limit"] == 10
    assert body["offset"] == 0
    assert body["total"] >= 7
    assert isinstance(body["items"], list) and len(body["items"]) >= 5

    first = body["items"][0]
    for field in ("id", "name", "slug", "trust_tier", "confidence"):
        assert field in first, f"missing field {field} on list item"


def test_list_strains_sorted_by_confidence_desc(client):
    resp = client.get("/api/v1/graph/strains?limit=20")
    items = resp.json()["items"]
    confidences = [it["confidence"] for it in items]
    assert confidences == sorted(confidences, reverse=True), (
        f"items not sorted by confidence desc: {confidences}"
    )


def test_list_strains_pagination_offset_and_limit(client):
    page1 = client.get("/api/v1/graph/strains?limit=3&offset=0").json()
    page2 = client.get("/api/v1/graph/strains?limit=3&offset=3").json()
    assert len(page1["items"]) == 3
    assert len(page2["items"]) == 3
    ids_p1 = {it["id"] for it in page1["items"]}
    ids_p2 = {it["id"] for it in page2["items"]}
    assert ids_p1.isdisjoint(ids_p2), "pagination overlapped"


def test_list_strains_limit_bounds(client):
    assert client.get("/api/v1/graph/strains?limit=0").status_code == 422
    assert client.get("/api/v1/graph/strains?limit=201").status_code == 422
    assert client.get("/api/v1/graph/strains?offset=-1").status_code == 422


def test_search_exact_match_wins(client):
    resp = client.get("/api/v1/graph/strains/search?q=blue-dream")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["query"] == "blue-dream"
    assert body["matches"], "no matches returned for an existing strain"
    assert body["matches"][0]["slug"] == "blue-dream"


def test_search_substring_returns_relevant_results(client):
    resp = client.get("/api/v1/graph/strains/search?q=cookies")
    assert resp.status_code == 200
    matches = resp.json()["matches"]
    assert any("girl-scout-cookies" in m["slug"] for m in matches), matches


def test_search_case_insensitive(client):
    upper = client.get("/api/v1/graph/strains/search?q=OG").json()
    lower = client.get("/api/v1/graph/strains/search?q=og").json()
    upper_ids = {m["id"] for m in upper["matches"]}
    lower_ids = {m["id"] for m in lower["matches"]}
    assert upper_ids == lower_ids
    assert upper_ids


def test_search_no_match_returns_empty(client):
    resp = client.get("/api/v1/graph/strains/search?q=zzzzzzqqq")
    assert resp.status_code == 200
    assert resp.json()["matches"] == []


def test_search_validates_empty_query(client):
    resp = client.get("/api/v1/graph/strains/search?q=")
    assert resp.status_code == 422


def test_search_respects_limit(client):
    resp = client.get("/api/v1/graph/strains/search?q=d&limit=2")
    body = resp.json()
    assert len(body["matches"]) <= 2
