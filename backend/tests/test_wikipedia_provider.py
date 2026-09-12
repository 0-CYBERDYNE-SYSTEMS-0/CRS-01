"""Pure helper tests for the cannabis-topic gate.

The live Wikipedia provider tests were removed — their coverage is
duplicated by the live-gated classes in test_research.py (CRS_LIVE_TESTS=1).
The provider/factory tests went with the deleted second search stack
(ingestion/search_provider.py).
"""
from __future__ import annotations

from src.ingestion.research.names import _looks_like_cannabis


def test_cannabis_keyword_detects_obvious_match():
    assert _looks_like_cannabis("Blue Dream (cannabis)", None, "A hybrid of Blueberry and Haze")


def test_cannabis_keyword_rejects_unrelated_page():
    assert not _looks_like_cannabis(
        "Blue Dream (2013 film)",
        "American drama film",
        "A drama film directed by Gregory Hatanaka starring James Duval",
    )


def test_cannabis_keyword_accepts_indica_page():
    assert _looks_like_cannabis(
        "Cannabis indica",
        None,
        "Cannabis indica is an annual plant species in the family Cannabaceae indigenous to the Hindu Kush mountains",
    )
