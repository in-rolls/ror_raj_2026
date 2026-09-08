import pytest

from rajasthan_ror.categorise import (
    CATEGORY_ORDER,
    ENTRY_KEYS,
    categorise,
    key,
    pieces,
    schedule_entries,
)


def test_schedules_load_and_validate():
    entries = schedule_entries()
    assert len(entries) == 378
    assert all(set(entry) == set(ENTRY_KEYS) for entry in entries)
    assert all(entry["category"] in CATEGORY_ORDER for entry in entries)
    triples = {(e["category"], e["entry_no"], e["synonym"]) for e in entries}
    assert len(triples) == len(entries)


def test_key_bridges_devanagari_and_roman():
    assert key("मेघवाल") == key("Meghwal") == key("Meghval")
    assert key("जाट") == key("Jat")
    assert key("बैरवा") == key("Bairwa") == key("Berwa")
    assert key("गुर्जर") == key("Gurjar")
    assert categorise("Gujjar")[0] == categorise("गुर्जर")[0] == "MBC"
    assert key("मीणा") == key("Mina") == key("Meena")


@pytest.mark.parametrize(
    ("jati", "category"),
    [
        ("मेघवाल", "SC"),
        ("भील", "ST"),
        ("जाट", "OBC"),
        ("गुर्जर", "MBC"),
        ("रैगर", "SC"),
        ("हरिजन", "unlisted"),
    ],
)
def test_schedule_lookups(jati, category):
    assert categorise(jati)[0] == category


def test_narrower_list_wins_and_glosses_are_tried():
    assert categorise("ढोली भील")[0] == "ST"
    assert categorise("ढोली")[0] == "SC"
    cat = categorise("मेर(मेहरात काठात,मेहरात-घोड़ात, चीता)")[0]
    assert cat in ("OBC", "unlisted")
    assert pieces("मेर(मेहरात काठात,मेहरात-घोड़ात, चीता)") == [
        "मेर",
        "मेहरात काठात",
        "मेहरात",
        "घोड़ात",
        "चीता",
    ]


def test_unlisted_is_not_general():
    assert categorise("राजपूत") == ("unlisted", None, "none")
    assert categorise("जैन")[0] == "unlisted"
    assert categorise(None) == ("unlisted", None, "none")
