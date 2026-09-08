import difflib
import sys

import pandas as pd

sys.argv = ["x"]
import pilot_join as pj  # noqa: E402

m = pj.match_villages(6)
own = pj.load_owners(m["giscode"].tolist())
kh = own.dropna(subset=["name"]).copy()
kh["nk"] = kh.name.map(pj.norm_name)
kh["fk"] = kh.relative.map(pj.norm_name)
kh = kh.drop_duplicates(["giscode", "nk", "fk"])
cards = pd.read_parquet(
    pj.MILAAN / "data/ration/cards/district_code=112/data_0.parquet"
)
cards = cards[cards.village_code.astype(str).isin(m.Village_Code.astype(str))].copy()
cards["giscode"] = cards.village_code.astype(str).map(
    dict(zip(m.Village_Code.astype(str), m.giscode, strict=True))
)
mem = pd.read_parquet(
    pj.MILAAN / "data/ration/members/district_code=112/data_0.parquet"
)
mem = mem[mem.card_no.isin(cards.card_no)].merge(
    cards[["card_no", "giscode"]], on="card_no"
)
mem["nk"] = mem.name_dev.map(pj.norm_name)
mem["fk"] = mem.member_father_dev.map(pj.norm_name)
ex = mem[["giscode", "nk", "fk"]].drop_duplicates().assign(exact=True)
kh = kh.merge(ex, on=["giscode", "nk", "fk"], how="left")
kh["exact"] = kh.exact.fillna(False).astype(bool)
c = kh[~kh.exact].merge(
    mem[
        ["giscode", "nk", "fk", "name_dev", "member_father_dev", "relationship_dev"]
    ].rename(columns={"fk": "fk_card"}),
    on=["giscode", "nk"],
    how="inner",
)
c["ratio"] = [
    difflib.SequenceMatcher(None, a, b).ratio()
    for a, b in zip(c.fk, c.fk_card, strict=True)
]
best = c.sort_values("ratio", ascending=False).drop_duplicates(["giscode", "nk", "fk"])
print(
    "khatedars: exact",
    int(kh.exact.sum()),
    "| name-only matched",
    len(best),
    "| no name match",
    int((~kh.exact).sum()) - len(best),
)
print(
    pd.cut(best.ratio, [0, 0.5, 0.7, 0.8, 0.9, 1.0], include_lowest=True)
    .value_counts()
    .sort_index()
    .to_string()
)
pd.set_option("display.width", 220)
print(
    best[best.ratio >= 0.7]
    .sample(min(15, len(best[best.ratio >= 0.7])), random_state=1)[
        [
            "name",
            "relation",
            "relative",
            "member_father_dev",
            "relationship_dev",
            "ratio",
        ]
    ]
    .to_string()
)
print(
    best[best.ratio < 0.5]
    .sample(10, random_state=1)[
        [
            "name",
            "relation",
            "relative",
            "name_dev",
            "member_father_dev",
            "relationship_dev",
        ]
    ]
    .to_string()
)
