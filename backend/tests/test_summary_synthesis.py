"""Evidence-grounded summary synthesis (kb.merge_research_run).

The subject strain's description is mined VERBATIM from the run's raw
provider evidence (raw/<run_id>/*.json) only when no summary exists —
LLM-provided or previously stored text is never overwritten, and a run
without raw evidence leaves the summary honestly NULL.
"""
import json
import re

from src.graph import kb


WIKI_SNIPPET = (
    "AK-47, also known simply as AK, is a cannabis strain with high THC "
    "content. It is a hybrid strain of cannabis that is sativa-dominant; it "
    "mixes Colombian, Mexican, Thai, and Afghan strains. A strong and "
    "popular strain, it has won multiple cannabis industry awards. A fourth "
    "sentence that must not appear in a three-sentence lead."
)

WIKI_URL = "https://en.wikipedia.org/wiki/AK-47_(cannabis)"


def _write_raw(raw_dir, rows):
    raw_dir.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(rows):
        (raw_dir / f"{i:012x}.json").write_text(
            json.dumps(row, ensure_ascii=False), encoding="utf-8"
        )


def _run(query="AK-47", run_id="run-1", raw_dir=None, strain_meta=None):
    run = {
        "query": query,
        "run_id": run_id,
        "started_at": 1724000000,
        "completed_at": 1724000500,
        "providers_used": ["test"],
        "sources_visited": [
            {"title": "AK-47 - Wikipedia", "url": WIKI_URL, "engine": "wikipedia"},
        ],
        "lineage_claims": [
            {
                "child": "AK-47", "parent_a": "Colombian", "parent_b": "Mexican",
                "extra_parents": ["Thai", "Afghani"],
                "source_url": WIKI_URL,
                "source_title": "AK-47 - Wikipedia", "source_engine": "wikipedia",
                "snippet_excerpt": "it mixes Colombian, Mexican, Thai",
                "confidence": 0.8, "raw_text": "",
            },
        ],
        "strain_meta": strain_meta or {},
    }
    if raw_dir is not None:
        run["raw_dir"] = str(raw_dir)
    return run


def test_merge_mines_verbatim_summary_with_attribution(tmp_path):
    # Known parents so the claims merge un-gated (the unresolved-parent
    # gate would otherwise hide them and drain sources_consulted).
    with kb.connect() as conn:
        for parent in ("Colombian", "Mexican", "Thai", "Afghani"):
            kb.upsert_strain(conn, parent)

    raw_dir = tmp_path / "ingest" / "raw" / "run-1"
    _write_raw(raw_dir, [
        # Off-topic row: mentions the name but fails the cannabis gate.
        {"title": "AK-47 rifle history", "url": "https://arms.example.com/ak-47",
         "snippet": "AK-47 is a gas-operated assault rifle developed in the Soviet Union.",
         "source": "tavily", "score": 0.9, "image_url": ""},
        # Seed-shop row: too thin, no cannabis tokens, loses on every axis.
        {"title": "Buy AK-47 seeds", "url": "https://seeds.example.com/ak47",
         "snippet": "AK-47 seeds for sale.", "source": "tavily", "score": 0.4,
         "image_url": ""},
        # The good candidate: wikipedia, descriptive, mentions the strain.
        {"title": "AK-47 - Wikipedia", "url": WIKI_URL,
         "snippet": WIKI_SNIPPET, "source": "wikipedia", "score": 1.0,
         "image_url": ""},
    ])

    counts = kb.merge_research_run(_run(raw_dir=raw_dir))
    assert counts["strains"] >= 1

    strain = kb.get_strain("AK-47")
    assert strain is not None
    data = strain["data"]
    assert data["summary"], "summary should be populated from raw evidence"

    # Verbatim: a whitespace-normalized character-for-character lead of the
    # source snippet, cut at a sentence boundary — never rewritten.
    flat = re.sub(r"\s+", " ", WIKI_SNIPPET)
    assert data["summary"] in flat
    assert data["summary"].endswith("awards.")
    assert "fourth sentence" not in data["summary"]
    assert len(data["summary"]) <= 400

    # Attribution travels with the words.
    assert data["summary_source_url"] == WIKI_URL
    assert data["summary_source_title"] == "AK-47 - Wikipedia"

    # get_neighborhood exposes summary, attribution, and sources_consulted.
    hood = kb.get_neighborhood("ak-47", depth=1)
    center = next(n for n in hood["nodes"] if n["id"] == "ak-47")
    assert center["data"]["summary"] == data["summary"]
    assert center["data"]["summary_source_url"] == WIKI_URL
    assert center["data"]["summary_source_title"] == "AK-47 - Wikipedia"
    assert hood["stats"]["sources_consulted"] >= 1


def test_existing_summary_never_overwritten_by_second_merge(tmp_path):
    raw1 = tmp_path / "ingest" / "raw" / "run-1"
    _write_raw(raw1, [
        {"title": "AK-47 - Wikipedia", "url": WIKI_URL,
         "snippet": WIKI_SNIPPET, "source": "wikipedia", "score": 1.0,
         "image_url": ""},
    ])
    kb.merge_research_run(_run(run_id="run-1", raw_dir=raw1))
    first = kb.get_strain("AK-47")["data"]
    assert first["summary"]
    assert first["summary_source_url"] == WIKI_URL

    # A second, different run must not replace the stored summary.
    raw2 = tmp_path / "ingest" / "raw" / "run-2"
    _write_raw(raw2, [
        {"title": "AK-47 strain review", "url": "https://leafly.example.com/ak-47",
         "snippet": "AK-47 is a sativa-dominant hybrid with award-winning resin "
                    "production and a sour aroma. It flowers quickly.",
         "source": "tavily", "score": 0.9, "image_url": ""},
    ])
    kb.merge_research_run(_run(run_id="run-2", raw_dir=raw2))
    second = kb.get_strain("AK-47")["data"]
    assert second["summary"] == first["summary"]
    assert second["summary_source_url"] == first["summary_source_url"]
    assert second["summary_source_title"] == first["summary_source_title"]


def test_llm_summary_wins_and_stays_unattributed(tmp_path):
    raw_dir = tmp_path / "ingest" / "raw" / "run-llm"
    _write_raw(raw_dir, [
        {"title": "AK-47 - Wikipedia", "url": WIKI_URL,
         "snippet": WIKI_SNIPPET, "source": "wikipedia", "score": 1.0,
         "image_url": ""},
    ])
    kb.merge_research_run(_run(
        run_id="run-llm",
        raw_dir=raw_dir,
        strain_meta={"ak-47": {"summary": "LLM-written hybrid description"}},
    ))
    data = kb.get_strain("AK-47")["data"]
    assert data["summary"] == "LLM-written hybrid description"
    # No URL backs an LLM sentence — attribution stays honestly NULL.
    assert data["summary_source_url"] is None
    assert data["summary_source_title"] is None


def test_missing_raw_dir_leaves_summary_null(tmp_path):
    # No raw_dir key at all.
    kb.merge_research_run(_run(run_id="run-no-raw"))
    data = kb.get_strain("AK-47")["data"]
    assert data["summary"] is None
    assert data["summary_source_url"] is None

    # A raw_dir that does not exist is tolerated the same way.
    kb.merge_research_run(_run(
        run_id="run-missing-dir",
        raw_dir=tmp_path / "does" / "not" / "exist",
    ))
    data = kb.get_strain("AK-47")["data"]
    assert data["summary"] is None
    assert data["summary_source_url"] is None
