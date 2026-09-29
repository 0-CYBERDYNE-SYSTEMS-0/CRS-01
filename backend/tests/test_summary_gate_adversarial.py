"""Adversarial fixtures for the cultivar summary gate.

twodog's review of PR #12 asked for cases the lexical first-sentence
heuristic is known to get wrong. Two groups:

(a) Alternate cultivar wording in the lead still passes when the page
    describes that strain: "variety", "hybrid cultivar named X", an
    appositive, "known as", "call this cultivar".
(b) The same name early in the sentence still fails when the page is a
    song, film, shop listing, or only a parent of a different cross.
    "is a parent of this hybrid" is not "is a hybrid". "The song X is a
    cannabis strain…" is still about the song.

The gate table calls ``_page_describes_cultivar`` directly. The merge
tests show mining honors the same answers: a passing lead is stored
verbatim, and a louder failing page cannot take the summary.
"""
import json

import pytest

from src.graph import kb


def _gate(name: str, title: str, text: str) -> bool:
    return kb._page_describes_cultivar(title, text, name)


# (a) The lead describes this strain, just not with the canonical
# "Name is a cannabis strain" copula — or it is a stored lead we must
# keep accepting (AK-47's alias bridge, Blue Dream's "Summary:" prefix).
_PASS = [
    pytest.param(
        "Blue Dream",
        "Blue Dream - Wikipedia",
        "Blue Dream is a popular cannabis variety with a sweet berry aroma "
        "and a balanced high.",
        id="variety-synonym",
    ),
    pytest.param(
        "Blue Dream",
        "Blue Dream - Wikipedia",
        "A hybrid cultivar named Blue Dream was bred in California from "
        "Blueberry and Haze.",
        id="hybrid-cultivar-named",
    ),
    pytest.param(
        "Blue Dream",
        "Blue Dream - Wikipedia",
        "Blue Dream, a sativa-dominant cannabis cultivar, flowers in about "
        "nine weeks.",
        id="appositive-cultivar",
    ),
    pytest.param(
        "Blue Dream",
        "Blue Dream - Wikipedia",
        "The cannabis strain known as Blue Dream is grown outdoors in Santa Cruz.",
        id="known-as",
    ),
    pytest.param(
        "Blue Dream",
        "Blue Dream - Wikipedia",
        "Breeders call this indica cultivar Blue Dream when the calyxes show purple.",
        id="call-this-cultivar",
    ),
    pytest.param(
        "Blue Dream",
        "Blue Dream - Wikipedia",
        "The cannabis strain Blue Dream was developed in California for its "
        "berry aroma.",
        id="noun-then-name",
    ),
    pytest.param(
        "Blue Dream",
        "Blue Dream - Wikipedia",
        '"Blue Dream" is a sativa-dominant hybrid grown for its berry aroma.',
        id="quoted-name-copula",
    ),
    pytest.param(
        "Gelato",
        "Gelato - Wikipedia",
        'A hybrid cultivar called "Gelato" was selected for its dessert aroma.',
        id="called-quoted-name",
    ),
    pytest.param(
        "OG Kush",
        "OG Kush - Wikipedia",
        "OG Kush is a marijuana strain with a diesel aroma and heavy resin.",
        id="marijuana-strain-synonym",
    ),
    pytest.param(
        "Northern Lights",
        "Northern Lights - Wikipedia",
        "Northern Lights is the flagship indica bred for dense resin production.",
        id="the-flagship-indica",
    ),
    pytest.param(
        "Purple Haze",
        "Purple Haze (cannabis)",
        "Purple Haze is a cannabis strain named after the song by Jimi Hendrix "
        "and grown for its aroma.",
        id="cultivar-named-after-a-song",
    ),
    pytest.param(
        "AK-47",
        "AK-47 (cannabis)",
        "AK-47, also known simply as AK, is a cannabis strain with high THC "
        "content. It is a hybrid strain of cannabis that is sativa-dominant.",
        id="stored-ak47-alias-bridge",
    ),
    pytest.param(
        "blue dream",
        "Wedding Cake Strain: Effects, THC & Medical Uses",
        "Summary: Blue Dream is a California-born sativa-dominant hybrid "
        "crossing Blueberry and Haze, renowned for its sweet blueberry aroma.",
        id="stored-blue-dream-summary-label",
    ),
]

# (b) The name is early. A loose "hybrid within N words" scan accepts
# these. The page subject is a song, a film, a shop listing, or another
# cross that only mentions this strain as a parent.
_REFUSE = [
    pytest.param(
        "Northern Lights",
        "Hawaiian Lights seeds",
        "Northern Lights is listed among the parents of this hybrid cultivar "
        "sold as Hawaiian Lights at the shop.",
        id="early-listed-among-parents",
    ),
    pytest.param(
        "Northern Lights",
        "Hawaiian Lights: Purest Indica x NL#5",
        "Northern Lights is a parent of this hybrid cultivar, Hawaiian Lights, "
        "a cannabis strain bred in Seattle.",
        id="early-is-a-parent-of-this-hybrid",
    ),
    pytest.param(
        "Blue Dream",
        "Wedding Cake seeds",
        "Blue Dream is crossed into our new hybrid cultivar named Wedding Cake, "
        "now in stock at the seed shop.",
        id="early-crossed-into-other-cultivar",
    ),
    pytest.param(
        "Gelato",
        "Seed shop — Wedding Cake",
        "Gelato is available beside this hybrid cultivar at the seed shop, and "
        "the cannabis strain on offer is Wedding Cake.",
        id="early-shop-beside-other-hybrid",
    ),
    pytest.param(
        "Girl Scout Cookies",
        "Wedding Cake seed listing",
        "Girl Scout Cookies is printed on a sticker beside this hybrid cultivar "
        "named Wedding Cake in the seed shop.",
        id="early-shop-sticker-other-cultivar",
    ),
    pytest.param(
        "Purple Haze",
        "Purple Haze - Wikipedia",
        '"Purple Haze" is a song by English electronic music duo Groove Armada, '
        "taken from their album Lovebox.",
        id="early-is-a-song",
    ),
    pytest.param(
        "Purple Haze",
        "Purple Haze (Groove Armada song)",
        "Purple Haze is a cannabis strain fans mention when they discuss the recording.",
        id="title-paren-song",
    ),
    pytest.param(
        "Purple Haze",
        "Purple Haze - Wikipedia",
        "The song Purple Haze is a cannabis strain only in the lyrics, not a "
        "cultivar profile anyone grows.",
        id="early-the-song-then-is-a-strain",
    ),
    pytest.param(
        "Purple Haze",
        "Purple Haze - Wikipedia",
        "Purple Haze, a psychedelic song by Hendrix, mentions cannabis without "
        "describing a strain.",
        id="early-appositive-song",
    ),
    pytest.param(
        "Purple Haze",
        "Purple Haze - Wikipedia",
        "Purple Haze is a variety of song about cannabis culture and nothing else.",
        id="variety-of-song",
    ),
    pytest.param(
        "White Widow",
        "White Widow - Wikipedia",
        "White Widow is a film about growers in Amsterdam, and the cannabis "
        "strain of that name is not profiled.",
        id="early-is-a-film",
    ),
    pytest.param(
        "White Widow",
        "White Widow - Wikipedia",
        "In the film White Widow, the protagonist never grows a cannabis strain "
        "or hybrid cultivar.",
        id="early-in-the-film-name",
    ),
    pytest.param(
        "White Widow",
        "White Widow - Wikipedia",
        "White Widow opens the film about a cannabis strain of the same name "
        "and never describes the cultivar.",
        id="early-opens-the-film",
    ),
    pytest.param(
        "Durban Poison",
        "Durban Poison - Wikipedia",
        "Durban Poison is the title of a film in which a cannabis strain is only a prop.",
        id="early-title-of-a-film",
    ),
    pytest.param(
        "Northern Lights",
        "Hawaiian Lights seeds",
        "The pollen parent of the hybrid Northern Lights appears on this shop "
        "page for Hawaiian Lights, a cannabis strain.",
        id="early-parent-of-the-hybrid-name",
    ),
    pytest.param(
        "Northern Lights",
        "Hawaiian Lights: Purest Indica x NL#5",
        "This hybrid, affectionately known as Hawaiian Lights, is a cross "
        "between Purest Indica and Northern Lights #5. The cannabis strain "
        "was bred in Seattle.",
        id="parent-only-different-cross",
    ),
]


@pytest.mark.parametrize("name,title,text", _PASS)
def test_alternate_cultivar_wording_passes(name, title, text):
    assert _gate(name, title, text)


@pytest.mark.parametrize("name,title,text", _REFUSE)
def test_early_same_name_non_cultivar_refuses(name, title, text):
    assert not _gate(name, title, text)


def _write_raw(raw_dir, rows):
    raw_dir.mkdir(parents=True, exist_ok=True)
    for i, row in enumerate(rows):
        (raw_dir / f"{i:012x}.json").write_text(
            json.dumps(row, ensure_ascii=False), encoding="utf-8"
        )


def _run(query, run_id, raw_dir):
    return {
        "query": query,
        "run_id": run_id,
        "started_at": 1724000000,
        "completed_at": 1724000500,
        "providers_used": ["test"],
        "sources_visited": [],
        "lineage_claims": [],
        "strain_meta": {},
        "raw_dir": str(raw_dir),
    }


def test_merge_mines_hybrid_cultivar_named_wording(tmp_path):
    """(a) through the merge path: the stored summary is that lead, verbatim."""
    raw_dir = tmp_path / "raw"
    snippet = (
        "A hybrid cultivar named Blue Dream was bred in California from "
        "Blueberry and Haze. It smells like berries."
    )
    url = "https://en.wikipedia.org/wiki/Blue_Dream"
    _write_raw(raw_dir, [{
        "title": "Blue Dream - Wikipedia",
        "url": url,
        "snippet": snippet,
        "source": "wikipedia",
        "score": 1.0,
        "image_url": "",
    }])
    kb.merge_research_run(_run("Blue Dream", "run-named", raw_dir))
    data = kb.get_strain("Blue Dream")["data"]
    assert data["summary"].startswith("A hybrid cultivar named Blue Dream")
    assert data["summary"] in snippet
    assert "berries." in data["summary"]
    assert data["summary_source_url"] == url
    assert data["trust_tier"] != "VERIFIED"


def test_merge_refuses_early_parent_mention(tmp_path):
    """(b) through the merge path: a shop page that names this strain first,
    as a parent of a different hybrid, does not become the summary."""
    raw_dir = tmp_path / "raw"
    _write_raw(raw_dir, [{
        "title": "Hawaiian Lights seeds",
        "url": "https://shop.example.com/hawaiian-lights",
        "snippet": (
            "Northern Lights is listed among the parents of this hybrid "
            "cultivar sold as Hawaiian Lights at the shop. The cannabis "
            "strain on the label is Hawaiian Lights."
        ),
        "source": "tavily",
        "score": 0.9,
        "image_url": "",
    }])
    kb.merge_research_run(_run("Northern Lights", "run-parent", raw_dir))
    data = kb.get_strain("Northern Lights")["data"]
    assert data["summary"] is None
    assert data["summary_source_url"] is None


def test_merge_early_song_strain_sentence_loses_to_cultivar_page(tmp_path):
    """(b) where the failing page would otherwise win the mine.

    Both rows are Wikipedia, and the song lead is longer, so length would
    pick it if the gate treated "The song Purple Haze is a cannabis strain…"
    as a cultivar description. The cultivar article has to win.
    """
    raw_dir = tmp_path / "raw"
    song_url = "https://en.wikipedia.org/wiki/Purple_Haze"
    cultivar_url = "https://en.wikipedia.org/wiki/Purple_Haze_(cannabis)"
    _write_raw(raw_dir, [
        {
            "title": "Purple Haze - Wikipedia",
            "url": song_url,
            "snippet": (
                "The song Purple Haze is a cannabis strain only in the lyrics "
                "of this recording by Jimi Hendrix, discussed here at length "
                "as a piece of music rather than a cultivar anyone grows in "
                "a garden or a room."
            ),
            "source": "wikipedia",
            "score": 1.0,
            "image_url": "",
        },
        {
            "title": "Purple Haze (cannabis)",
            "url": cultivar_url,
            "snippet": (
                "Purple Haze is a cannabis strain, a sativa-dominant hybrid."
            ),
            "source": "wikipedia",
            "score": 1.0,
            "image_url": "",
        },
    ])
    kb.merge_research_run(_run("Purple Haze", "run-song-early", raw_dir))
    data = kb.get_strain("Purple Haze")["data"]
    assert data["summary_source_url"] == cultivar_url
    assert data["summary"].startswith("Purple Haze is a cannabis strain")
    assert "lyrics" not in data["summary"].lower()
