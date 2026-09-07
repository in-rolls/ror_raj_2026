"""Pilot: do Bhu-Naksha khatedars join to ration cards, and is jati filled?

Steps: (1) match Nagaur ration villages to portal villages by name; (2) pick
the N largest matched villages; (3) print the giscodes to fetch; (4) after the
fetch, parse and categorise, then join khatedar (name, father) to ration
applicant (name, father) within the village and report join rates.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd

ROR = Path("/Users/soodoku/Documents/GitHub/rajasthan-ror")
MILAAN = Path("/Users/soodoku/Documents/GitHub/milaan_raj")
OUT = ROR / "raw" / "pilot"
sys.path.insert(0, str(ROR))

from categorise import categorise, key  # noqa: E402
from parse_plots import rows_from_record  # noqa: E402

NAGAUR_RATION = 112
NAGAUR_PORTAL = "21"


def norm_village(s: str) -> str:
    s = unicodedata.normalize("NFC", str(s))
    s = re.sub(r"\(.*?\)", "", s)
    s = re.sub(r"[\s\-\.]", "", s)
    return s.replace("ङ", "ड़").replace("ड़", "ड").replace("ढ़", "ढ")


def norm_name(s: str) -> str:
    """Name key: NFC, drop honorifics and spacing, then the phonetic key."""
    s = unicodedata.normalize("NFC", str(s or ""))
    s = re.sub(r"\b(श्री|श्रीमती|स्व\.?|स्वर्गीय)\b", " ", s)
    s = re.sub(r"[\s\.\-]", "", s)
    return key(s)


SUFFIX = re.compile(r"(rm|ll|sing|sinh|cnd|cndr|ds|kumr)$")
# a land-record relation and a card relationship that cannot be the same person:
# a daughter of X is not the wife of X, and the card's father field is the husband for a wife
INCOMPATIBLE = {
    ("पुत्री", "पत्नी"),
    ("पत्नि", "बेटी"),
    ("पत्नी", "बेटी"),
    ("पुत्र", "पत्नी"),
    ("पुत्र", "बेटी"),
    ("पुत्री", "बेटा"),
    ("पत्नि", "बेटा"),
}


def compatible(df: pd.DataFrame) -> pd.Series:
    return ~pd.Series(list(zip(df["relation"], df["relationship_dev"])), index=df.index).isin(
        INCOMPATIBLE
    )


def loose(k: str) -> str:
    """Key with a trailing name suffix removed: गोपीराम and गोपी, शंकरलाल and शंकर, agree."""
    s = SUFFIX.sub("", k)
    return s if len(s) >= 3 else k


def match_villages(top: int) -> pd.DataFrame:
    vil = pd.read_parquet(ROR / "raw" / "villages.parquet")
    vil = vil[vil["district_code"] == NAGAUR_PORTAL].copy()
    vil["vkey"] = vil["village_name"].map(norm_village)
    rat = pd.read_csv(MILAAN / "data/raw/rural_village.csv.gz")
    rat = rat[rat["District_Code"] == NAGAUR_RATION].copy()
    rat["vkey"] = rat["Village"].map(norm_village)
    # one portal village may have several sheets; keep the current-survey ones
    cur = vil[~vil["village_name"].str.contains("पुराना", na=False)]
    m = rat.merge(cur, on="vkey", how="inner")
    amb = m.groupby("vkey")["giscode"].nunique()
    m = m[m["vkey"].isin(amb[amb == 1].index)]
    m = m.sort_values("Total", ascending=False)
    print(
        f"ration villages {len(rat)}, portal villages {cur['vkey'].nunique()}, "
        f"name-matched {m['vkey'].nunique()} (unambiguous)"
    )
    return m.head(top)


def load_owners(giscodes: list[str]) -> pd.DataFrame:
    import gzip
    import json

    rows = []
    for g in giscodes:
        p = ROR / "raw" / "plots" / f"{g}.jsonl.gz"
        if not p.exists():
            continue
        recs = []
        try:
            with gzip.open(p, "rt", encoding="utf-8") as fh:
                for line in fh:
                    recs.append(json.loads(line))
        except (EOFError, OSError):
            pass
        by_plot = {r["plotno"]: r["data"] for r in recs if r.get("ok") and r.get("data")}
        for r in recs:
            rows.extend(rows_from_record(r, by_plot))
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--join", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    m = match_villages(a.top)
    m[["Village", "Total", "Village_Code", "village_name", "giscode"]].to_csv(
        OUT / "pilot_villages.csv", index=False
    )
    print(m[["Village", "Total", "village_name", "giscode"]].to_string())
    print("giscodes:", ",".join(m["giscode"]))
    if not a.join:
        return

    own = load_owners(m["giscode"].tolist())
    own.to_parquet(OUT / "owners.parquet", index=False)
    print(
        f"\nowner rows {len(own)}, plots {own['plotno'].nunique()}, khatas {own['khata'].nunique()}"
    )
    print("jati filled:", f"{own['jati'].notna().mean():.1%}")
    cat = own["jati"].map(lambda j: categorise(j)[0])
    own["category"] = cat
    print("category split:\n", cat.value_counts(normalize=True).round(3).to_string())
    print("top jati strings:\n", own["jati"].value_counts().head(30).to_string())
    own[["jati", "category"]].drop_duplicates().sort_values("category").to_csv(
        OUT / "jati_strings.csv", index=False
    )

    # ration side: applicants of cards in the pilot villages
    cards = pd.read_parquet(
        MILAAN / f"data/ration/cards/district_code={NAGAUR_RATION}/data_0.parquet"
    )
    cards = cards[cards["village_code"].astype(str).isin(m["Village_Code"].astype(str))].copy()
    vmap = dict(zip(m["Village_Code"].astype(str), m["giscode"]))
    cards["giscode"] = cards["village_code"].astype(str).map(vmap)
    cards["nk"] = cards["applicant_dev"].map(norm_name)
    cards["fk"] = cards["father_dev"].map(norm_name)
    cards["nl"], cards["fl"] = cards["nk"].map(loose), cards["fk"].map(loose)

    # land side: distinct khatedars (an owner appears once per plot)
    kh = own.dropna(subset=["name"]).copy()
    kh["nk"] = kh["name"].map(norm_name)
    kh["fk"] = kh["relative"].map(norm_name)
    kh = kh.drop_duplicates(["giscode", "nk", "fk"])
    kh["nl"], kh["fl"] = kh["nk"].map(loose), kh["fk"].map(loose)

    # khatedar -> card: of the landholders fetched so far, how many have a ration card here?
    ck = cards.drop_duplicates(["giscode", "nk", "fk"])[["giscode", "nk", "fk", "card_no"]]
    k2c = kh.merge(ck, on=["giscode", "nk", "fk"], how="left")
    kh_hit = k2c["card_no"].notna()
    print(
        f"\nkhatedars (distinct name+father) fetched so far: {len(kh)}; "
        f"with a ration card in the village on name+father: {kh_hit.sum()} ({kh_hit.mean():.1%})"
    )
    k2c_name = kh.merge(
        cards.drop_duplicates(["giscode", "nk"])[["giscode", "nk", "card_no"]],
        on=["giscode", "nk"],
        how="left",
    )
    print(
        f"  on name only: {k2c_name['card_no'].notna().sum()} ({k2c_name['card_no'].notna().mean():.1%})"  # noqa: E501
    )
    print(
        "  by relation:",
        k2c.assign(hit=kh_hit)
        .groupby("relation")["hit"]
        .agg(["mean", "size"])
        .round(3)
        .to_dict("index"),
    )
    print(
        "  by category:",
        k2c.assign(hit=kh_hit)
        .groupby("category")["hit"]
        .agg(["mean", "size"])
        .round(3)
        .to_dict("index"),
    )
    miss = k2c.loc[~kh_hit, ["name", "relative", "jati"]].sample(
        min(12, int((~kh_hit).sum())), random_state=3
    )
    print("  sample of unmatched khatedars:\n", miss.to_string())

    # khatedar -> any member of any card in the village (sons are members on the father's card)
    mem = pd.read_parquet(
        MILAAN / f"data/ration/members/district_code={NAGAUR_RATION}/data_0.parquet"
    )
    mem = mem[mem["card_no"].isin(cards["card_no"])].merge(
        cards[["card_no", "giscode", "card_type_raw"]], on="card_no"
    )
    mem["nk"] = mem["name_dev"].map(norm_name)
    mem["fk"] = mem["member_father_dev"].map(norm_name)
    mem["nl"], mem["fl"] = mem["nk"].map(loose), mem["fk"].map(loose)
    mk = mem.drop_duplicates(["giscode", "nk", "fk"])[
        ["giscode", "nk", "fk", "card_no", "relationship_dev"]
    ]
    k2m = kh.merge(mk, on=["giscode", "nk", "fk"], how="left")
    m_hit = k2m["card_no"].notna()
    print(
        f"\nkhatedar -> any card MEMBER on name+father: {m_hit.sum()} ({m_hit.mean():.1%}) of {len(kh)}; "  # noqa: E501
        f"members in pilot villages: {len(mem)}"
    )
    print(
        "  by relation:",
        k2m.assign(hit=m_hit)
        .groupby("relation")["hit"]
        .agg(["mean", "size"])
        .round(3)
        .to_dict("index"),
    )
    print(
        "  matched member's relationship on card:",
        k2m.loc[m_hit, "relationship_dev"].value_counts().to_dict(),
    )
    ml = mem.drop_duplicates(["giscode", "nl", "fl", "relationship_dev"])[
        ["giscode", "nl", "fl", "card_no", "relationship_dev"]
    ]
    k2l = kh.merge(ml, on=["giscode", "nl", "fl"], how="left")
    k2l = k2l[k2l["card_no"].isna() | compatible(k2l)].drop_duplicates(["giscode", "nk", "fk"])
    l_hit = k2l["card_no"].notna()
    print(
        f"khatedar -> any card MEMBER on suffix-stripped name+father: {l_hit.sum()} ({l_hit.mean():.1%})"  # noqa: E501
    )
    print(
        "  by relation:",
        k2l.assign(hit=l_hit)
        .groupby("relation")["hit"]
        .agg(["mean", "size"])
        .round(3)
        .to_dict("index"),
    )
    print(
        "  by category:",
        k2l.assign(hit=l_hit)
        .groupby("category")["hit"]
        .agg(["mean", "size"])
        .round(3)
        .to_dict("index"),
    )
    c2l = mem.merge(
        kh[["giscode", "nl", "fl", "relation", "jati", "category"]],
        on=["giscode", "nl", "fl"],
        how="inner",
    )
    c2l = c2l[compatible(c2l)]
    cl = c2l.groupby("card_no").agg(jati=("jati", "first"), n_jati=("jati", "nunique"))
    print(
        f"  cards with >=1 khatedar member (loose): {len(cl)} ({len(cl) / len(cards):.1%}); disagreeing on jati: {(cl['n_jati'] > 1).sum()}"  # noqa: E501
    )
    # ambiguity: one khatedar key hitting members on several cards, or one card hit by several khatedar keys  # noqa: E501
    kk = c2l.groupby(["giscode", "nl", "fl"])["card_no"].nunique()
    print(
        f"  khatedar keys matching members on >1 card: {(kk > 1).sum()} of {len(kk)} ({(kk > 1).mean():.1%})"  # noqa: E501
    )
    ck2 = c2l.groupby("card_no").apply(lambda d: d[["nl", "fl"]].drop_duplicates().shape[0])
    print(f"  cards hit by >1 distinct khatedar key: {(ck2 > 1).sum()} of {len(ck2)}")
    unamb = cl[(cl["n_jati"] == 1)]
    print(
        f"  cards with a single, unambiguous jati: {len(unamb)} ({len(unamb) / len(cards):.1%} of cards)"  # noqa: E501
    )

    # card -> jati via any member who is a khatedar
    c2j = mem.merge(
        kh[["giscode", "nk", "fk", "jati", "category"]], on=["giscode", "nk", "fk"], how="inner"
    )
    cj = c2j.groupby("card_no").agg(
        jati=("jati", "first"), category=("category", "first"), n_jati=("jati", "nunique")
    )
    print(
        f"cards with >=1 khatedar member: {len(cj)} ({len(cj) / len(cards):.1%} of {len(cards)}); "
        f"cards whose khatedar members disagree on jati: {(cj['n_jati'] > 1).sum()}"
    )

    j = cards.merge(
        kh[["giscode", "nk", "fk", "jati", "category"]], on=["giscode", "nk", "fk"], how="left"
    )
    hit = j["jati"].notna()
    print(f"\nration cards in pilot villages: {len(cards)}; khatedars: {len(kh)}")
    print(f"cards joined on name+father within village: {hit.sum()} ({hit.mean():.1%})")
    j2 = cards.merge(
        kh[["giscode", "nk", "jati"]].drop_duplicates(["giscode", "nk"]),
        on=["giscode", "nk"],
        how="left",
    )
    print(
        f"cards joined on name only within village: {j2['jati'].notna().sum()} ({j2['jati'].notna().mean():.1%})"  # noqa: E501
    )
    print(
        "\ncategory of joined cards:\n",
        j.loc[hit, "category"].value_counts(normalize=True).round(3).to_string(),
    )
    print(
        "\ncard type by joined/not:\n",
        pd.crosstab(j["card_type_raw"], hit, normalize="columns").round(3).to_string(),
    )
    sample = j.loc[
        hit, ["applicant_dev", "father_dev", "card_type_raw", "jati", "category"]
    ].sample(min(30, int(hit.sum())), random_state=1)
    sample.to_csv(OUT / "joined_sample.csv", index=False)
    print("\nsample of joined cards:\n", sample.to_string())


if __name__ == "__main__":
    main()
