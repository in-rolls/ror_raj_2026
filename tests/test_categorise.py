import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from categorise import categorise, key, pieces  # noqa: E402


def test_key_bridges_devanagari_and_roman():
    assert key("मेघवाल") == key("Meghwal") == key("Meghval")
    assert key("जाट") == key("Jat")
    assert key("बैरवा") == key("Bairwa") == key("Berwa")
    assert key("गुर्जर") == key("Gurjar")
    assert categorise("Gujjar")[0] == categorise("गुर्जर")[0] == "MBC"
    assert key("मीणा") == key("Mina") == key("Meena")


def test_schedule_lookups():
    assert categorise("मेघवाल")[0] == "SC"
    assert categorise("भील")[0] == "ST"
    assert categorise("जाट")[0] == "OBC"
    assert categorise("गुर्जर")[0] == "MBC"
    assert categorise("रैगर")[0] == "SC"
    assert categorise("हरिजन")[0] == "unlisted"


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
