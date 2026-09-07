"""Split fetched plot records into one row per khatedar.

Reads only ``raw/plots/`` and writes ``raw/owners.parquet``, so a parser
change never costs a refetch.

The ``info`` field is a short free-text block:

    क्षेत्रफल  : 0.0600 Hectare
    खाता संख्या   : 847
    1.) गुलाब सिंह चीता पुत्र अमर सिंह   हिस्सा- 2/3 जाति- मेर(मेहरात काठात, चीता) सा. अजयसर खातेदार
    2.) सुगरा पत्नि खंगार सिंह   हिस्सा- 1/3 जाति- मेर(मेहरात काठात, चीता)  सा देह खातेदार

Each numbered line is one co-owner: a name, then a relation word (``पुत्र``
son of, ``पत्नि`` wife of, ...) and the relative, then ``हिस्सा-`` the share,
``जाति-`` the caste, a residence introduced by ``सा.`` (*sakin*, resident
of; ``देह`` means this very village) or ``निवासी`` or an urban address block
starting ``मकान संख्या-``, and a closing tenure word such as ``खातेदार``.

The parse is a marker scan, not one whole-line regex, for the reason the
Odisha parser gives: a single regex silently drops every shape it did not
anticipate, and the shapes here already include institutional owners with no
relation and no caste, a caste with a parenthesised, comma-bearing gloss, and
a residence that is an address rather than a village. Every row keeps the
raw line so a parse can be audited against its source.

Usage:
    uv run python parse_plots.py
"""

from __future__ import annotations

import gzip
import json
import re
from collections import Counter
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
PLOTS = HERE / "raw" / "plots"
OWNERS = HERE / "raw" / "owners.parquet"

RELATIONS = ("पुत्र", "पुत्री", "पत्नि", "पत्नी", "विधवा", "पौत्र", "पौत्री", "माता", "पिता")
SHARE = "हिस्सा-"
CASTE = "जाति-"
RESIDENCE = re.compile(r"\s(?:सा\.|सा(?=\s)|निवासी|मकान संख्या-)")
TENURE = re.compile(r"\s(गैर[\s-]?खातेदार|सह[\s-]?खातेदार|खातेदार|मुरब्बा|अभिलेख)\s*$")
OWNER_LINE = re.compile(r"^\s*(\d+)\.\)\s*(.*)$")
AREA = re.compile(r"क्षेत्रफल\s*:\s*([\d.]+)")
KHATA = re.compile(r"खाता संख्या\s*:\s*(\S+)")
RELATION = re.compile(r"\s(" + "|".join(RELATIONS) + r")\s")


def split_owner(line: str) -> dict:
    """One numbered line into fields. Missing markers yield None, never a drop."""
    m = OWNER_LINE.match(line)
    if not m:
        raise ValueError(f"not an owner line: {line!r}")
    seq, rest = int(m.group(1)), m.group(2).strip()
    out: dict = {"owner_seq": seq, "raw_line": rest}

    tenure = TENURE.search(rest)
    if tenure:
        out["tenure"] = tenure.group(1)
        rest = rest[: tenure.start()]
    else:
        out["tenure"] = None

    head, _, tail = rest.partition(SHARE)
    if not _:
        head, tail = rest, ""
    rel = RELATION.search(" " + head + " ")
    if rel:
        out["name"] = head[: rel.start()].strip()
        out["relation"] = rel.group(1)
        out["relative"] = head[rel.end() - 1 :].strip()
    else:
        out["name"], out["relation"], out["relative"] = head.strip(), None, None

    share, _, after = tail.partition(CASTE)
    if _:
        res = RESIDENCE.search(after)
        out["jati"] = (after[: res.start()] if res else after).strip() or None
        out["residence"] = after[res.start() :].strip() if res else None
    else:
        res = RESIDENCE.search(share)
        out["jati"] = None
        out["residence"] = share[res.start() :].strip() if res else None
        share = share[: res.start()] if res else share
    out["share"] = share.strip() or None
    return out


def split_info(info: str) -> dict:
    lines = [ln for ln in info.split("\n") if ln.strip()]
    area = next((AREA.search(ln) for ln in lines if AREA.search(ln)), None)
    khata = next((KHATA.search(ln) for ln in lines if KHATA.search(ln)), None)
    owners = [split_owner(ln) for ln in lines if OWNER_LINE.match(ln)]
    return {
        "area_ha": float(area.group(1)) if area else None,
        "khata": khata.group(1) if khata else None,
        "owners": owners,
    }


def rows_from_record(rec: dict, by_plot: dict[str, dict] | None = None) -> list[dict]:
    """Owner rows for one fetched record; a ``via`` record borrows its source's data."""
    if not rec.get("ok"):
        return []
    d = rec.get("data")
    if d is None and rec.get("via") and by_plot:
        d = by_plot.get(rec["via"])
    if not d:
        return []
    parsed = split_info(d.get("info") or "")
    base = {
        "giscode": rec["giscode"],
        "plotno": rec["plotno"],
        "plotid": d.get("plotid"),
        "khata": parsed["khata"],
        "area_ha": parsed["area_ha"],
        "n_owners": len(parsed["owners"]),
        "via": rec.get("via"),
    }
    return [{**base, **o} for o in parsed["owners"]]


def main() -> None:
    rows: list[dict] = []
    files = sorted(PLOTS.glob("*.jsonl.gz"))
    for p in files:
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            recs = [json.loads(line) for line in fh]
        by_plot = {r["plotno"]: r["data"] for r in recs if r.get("ok") and r.get("data")}
        for r in recs:
            rows.extend(rows_from_record(r, by_plot))
    df = pd.DataFrame(rows)
    df.to_parquet(OWNERS, index=False)
    print(f"{len(files)} sheets, {len(df)} owner rows -> {OWNERS}")
    if len(df):
        print("jati filled:", f"{df['jati'].notna().mean():.1%}")
        print("relation filled:", f"{df['relation'].notna().mean():.1%}")
        print("tenure values:", Counter(df["tenure"]).most_common(8))
        print("top jati:", Counter(df["jati"].dropna()).most_common(25))


if __name__ == "__main__":
    main()
