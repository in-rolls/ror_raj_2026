import sys

import pandas as pd

from rajasthan_ror.categorise import categorise

sys.argv = ["x"]
import pilot_join as pj  # noqa: E402

m = pj.match_villages(6)
own = pj.load_owners(m["giscode"].tolist())
kh = own.dropna(subset=["name"]).copy()
kh["nk"] = kh.name.map(pj.norm_name)
kh["fk"] = kh.relative.map(pj.norm_name)
kh = kh.drop_duplicates(["giscode", "nk", "fk"])
kh["nl"], kh["fl"] = kh.nk.map(pj.loose), kh.fk.map(pj.loose)
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
    cards[["card_no", "giscode", "card_type_raw"]], on="card_no"
)
mem["nk"] = mem.name_dev.map(pj.norm_name)
mem["fk"] = mem.member_father_dev.map(pj.norm_name)
mem["nl"], mem["fl"] = mem.nk.map(pj.loose), mem.fk.map(pj.loose)
j = kh.merge(mem, on=["giscode", "nl", "fl"], suffixes=("", "_card"))
j = j[pj.compatible(j)]
j["tier"] = (j.nk == j.nk_card) & (j.fk == j.fk_card)
j["tier"] = j.tier.map({True: "exact", False: "loose"})
cols = [
    "tier",
    "name",
    "relation",
    "relative",
    "jati",
    "name_dev",
    "member_father_dev",
    "relationship_dev",
    "age_2021",
    "card_type_raw",
]
pd.set_option("display.width", 250)
print(
    "matched pairs:",
    len(j),
    "| distinct khatedars",
    j.drop_duplicates(["giscode", "nk", "fk"]).shape[0],
    "| khatedars matching >1 member:",
    (j.groupby(["giscode", "nk", "fk"]).size() > 1).sum(),
)
print(j[j.tier == "loose"].sample(20, random_state=4)[cols].to_string())
print(j[j.tier == "exact"].sample(10, random_state=4)[cols].to_string())
j[[*cols, "card_no", "plotno", "khata"]].to_csv(
    "raw/pilot/matched_pairs.csv", index=False
)
j["cat"] = j.jati.map(lambda x: categorise(x)[0])
g = (
    j.dropna(subset=["jati"])
    .groupby("card_no")
    .agg(n=("jati", "nunique"), jatis=("jati", lambda s: " | ".join(sorted(set(s)))))
)
print("cards with disagreeing jati:")
print(g[g.n > 1].to_string())
