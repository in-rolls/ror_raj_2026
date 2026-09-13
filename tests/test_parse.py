import pytest

from rajasthan_ror.parse import rows_from_record, split_info, split_owner
from rajasthan_ror.plots import integer_plots

RURAL = (
    "1.) गुलाब सिंह चीता पुत्र अमर सिंह   हिस्सा- 2/3 "
    "जाति- मेर(मेहरात काठात,मेहरात-घोड़ात, चीता) सा. अजयसर खातेदार"
)
WIFE = (
    "2.) सुगरा पत्नि खंगार सिंह   हिस्सा- 1/3 "
    "जाति- मेर(मेहरात काठात,मेहरात-घोड़ात, चीता)  सा देह खातेदार"
)
URBAN = (
    "1.) मंजू जैन पत्नि मनीष जैन   हिस्सा- पूर्ण  जाति- जैन मकान संख्या- S-8, "
    "कॉलोनी- पार्श्वनाथ कॉलोनी,  क्षेत्र/स्थान- वैशाली नगर,  शहर- अजमेर, "
    "पिन कोड- 305001, ज़िला - AJMER,राज्य- RAJASTHAN खातेदार"
)
INSTITUTION = "1.) अजमेर विकास प्राधिकरण अजमेर   हिस्सा- पूर्ण    अजमेर विकास प्राधिकरण  "


def test_rural_owner_with_glossed_caste():
    o = split_owner(RURAL)
    assert o["name"] == "गुलाब सिंह चीता"
    assert o["relation"] == "पुत्र"
    assert o["relative"] == "अमर सिंह"
    assert o["share"] == "2/3"
    assert o["jati"] == "मेर(मेहरात काठात,मेहरात-घोड़ात, चीता)"
    assert o["residence"] == "सा. अजयसर"
    assert o["tenure"] == "खातेदार"


def test_wife_and_bare_sa_marker():
    o = split_owner(WIFE)
    assert o["name"] == "सुगरा"
    assert o["relation"] == "पत्नि"
    assert o["relative"] == "खंगार सिंह"
    assert o["share"] == "1/3"
    assert o["residence"] == "सा देह"


def test_urban_address_block_does_not_leak_into_caste():
    o = split_owner(URBAN)
    assert o["jati"] == "जैन"
    assert o["residence"].startswith("मकान संख्या- S-8")
    assert o["residence"].endswith("RAJASTHAN")
    assert o["tenure"] == "खातेदार"


def test_institution_has_no_relation_or_caste_and_is_kept():
    o = split_owner(INSTITUTION)
    assert o["name"] == "अजमेर विकास प्राधिकरण अजमेर"
    assert o["relation"] is None
    assert o["jati"] is None
    assert o["share"] == "पूर्ण    अजमेर विकास प्राधिकरण"
    assert o["raw_line"].startswith("अजमेर")


def test_info_block_and_record_rows():
    info = "क्षेत्रफल  : 0.0600 Hectare\nखाता संख्या   : 847\n" + RURAL + "\n" + WIFE + "\n"
    p = split_info(info)
    assert p["area_ha"] == 0.06
    assert p["khata"] == "847"
    assert [o["owner_seq"] for o in p["owners"]] == [1, 2]
    rec = {
        "ok": True,
        "giscode": "0100207450292011035001",
        "plotno": "4",
        "data": {"info": info},
    }
    rows = rows_from_record(rec)
    assert len(rows) == 2
    assert rows[0]["n_owners"] == 2
    assert rows[1]["plotno"] == "4"
    assert rows_from_record({"ok": False, "giscode": "x", "plotno": "1"}) == []


def test_via_record_borrows_its_source():
    info = "खाता संख्या   : 9\n" + RURAL + "\n"
    src = {
        "ok": True,
        "giscode": "g",
        "plotno": "7",
        "data": {"info": info, "ownerplots": ["7", "8"]},
    }
    via = {"ok": True, "giscode": "g", "plotno": "8", "via": "7"}
    by_plot = {"7": src["data"]}
    rows = rows_from_record(via, by_plot)
    assert len(rows) == 1
    assert rows[0]["plotno"] == "8"
    assert rows[0]["via"] == "7"
    assert rows_from_record(via, None) == []


def test_integer_plots_reads_both_shapes():
    assert integer_plots(["3", "12", "287/807"]) == {"3", "12"}
    assert integer_plots("['3', '12']") == {"3", "12"}
    assert integer_plots(None) == set()


def test_unnumbered_government_owner_is_preserved_without_invented_sequence():
    info = "क्षेत्रफल : 0.5 Hectare\nखाता संख्या : 1\nराज. सरकार\n"
    owner = split_info(info)["owners"][0]
    assert owner["name"] == "राज. सरकार"
    assert owner["raw_line"] == "राज. सरकार"
    assert owner["owner_seq"] is None
    assert owner["jati"] is None
    assert split_info("क्षेत्रफल : 0.5 Hectare\nखाता संख्या : 1")["owners"] == []


@pytest.mark.parametrize(
    "boundary", ["गुलाब सिंह", "अमर सिंह", "मेर(मेहरात काठात", "सा. अजयसर"]
)
def test_owner_continuations_preserve_all_fields(boundary):
    wrapped = RURAL.replace(boundary, boundary.replace(" ", "\n", 1))
    info = "खाता संख्या : 847\n" + wrapped + "\n" + WIFE
    owners = split_info(info)["owners"]
    expected = split_owner(RURAL)
    assert len(owners) == 2
    assert owners[0]["raw_line"] == wrapped.partition("1.) ")[2]
    assert {k: v for k, v in owners[0].items() if k != "raw_line"} == {
        k: v for k, v in expected.items() if k != "raw_line"
    }
    assert owners[1] == split_owner(WIFE)


def test_metadata_and_government_lines_end_owner_continuations():
    info = RURAL + "\nराज. सरकार\nखाता संख्या : 847\nक्षेत्रफल : 0.06 Hectare"
    owners = split_info(info)["owners"]
    assert len(owners) == 2
    assert owners[0] == split_owner(RURAL)
    assert owners[1]["name"] == "राज. सरकार"
    assert owners[1]["owner_seq"] is None
