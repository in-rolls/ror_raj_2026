"""Split fetched plot records into one row per khatedar.

Reads only ``raw/plots/`` and writes ``raw/owners.parquet`` (both under the
current working directory; see :mod:`rajasthan_ror.paths`), so a parser
change never costs a refetch.

The ``info`` field is a short free-text block:

    क्षेत्रफल  : 0.0600 Hectare
    खाता संख्या   : 847
    1.) गुलाब सिंह चीता पुत्र अमर सिंह   हिस्सा- 2/3 जाति- मेर(मेहरात, चीता) सा. अजयसर खातेदार
    2.) सुगरा पत्नि खंगार सिंह   हिस्सा- 1/3 जाति- मेर(मेहरात, चीता)  सा देह खातेदार

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
    uv run rajasthan-ror-parse
"""

from __future__ import annotations

import gzip
import json
import logging
import re
from collections import Counter
from typing import Any

import pandas as pd

from rajasthan_ror.paths import OWNERS_FILE, PLOTS_DIR

log = logging.getLogger(__name__)

RELATIONS = (
    "पुत्र",
    "पुत्री",
    "पत्नि",
    "पत्नी",
    "विधवा",
    "पौत्र",
    "पौत्री",
    "माता",
    "पिता",
)
SHARE_MARKER = "हिस्सा-"
CASTE_MARKER = "जाति-"
RESIDENCE = re.compile(r"\s(?:सा\.|सा(?=\s)|निवासी|मकान संख्या-)")
TENURE = re.compile(r"\s(गैर[\s-]?खातेदार|सह[\s-]?खातेदार|खातेदार|मुरब्बा|अभिलेख)\s*$")
OWNER_LINE = re.compile(r"^\s*(\d+)\.\)\s*(.*)$")
AREA = re.compile(r"क्षेत्रफल\s*:\s*([\d.]+)")
KHATA = re.compile(r"खाता संख्या\s*:\s*(\S+)")
RELATION = re.compile(r"\s(" + "|".join(RELATIONS) + r")\s")


def split_owner(line: str) -> dict[str, Any]:
    """Split one numbered owner line into fields.

    A missing marker yields ``None`` for that field, never a dropped row.

    Args:
        line: One ``N.) ...`` line of the ``info`` block.

    Returns:
        ``owner_seq``, ``raw_line``, ``name``, ``relation``, ``relative``,
        ``share``, ``jati``, ``residence`` and ``tenure``.

    Raises:
        ValueError: When the line does not start with an owner number.
    """
    match = OWNER_LINE.match(line)
    if not match:
        raise ValueError(f"not an owner line: {line!r}")
    seq, rest = int(match.group(1)), match.group(2).strip()
    owner: dict[str, Any] = {"owner_seq": seq, "raw_line": rest}

    tenure = TENURE.search(rest)
    if tenure:
        owner["tenure"] = tenure.group(1)
        rest = rest[: tenure.start()]
    else:
        owner["tenure"] = None

    head, share_marker, tail = rest.partition(SHARE_MARKER)
    if not share_marker:
        head, tail = rest, ""
    relation = RELATION.search(" " + head + " ")
    if relation:
        owner["name"] = head[: relation.start()].strip()
        owner["relation"] = relation.group(1)
        owner["relative"] = head[relation.end() - 1 :].strip()
    else:
        owner["name"], owner["relation"], owner["relative"] = head.strip(), None, None

    share, caste_marker, after = tail.partition(CASTE_MARKER)
    if caste_marker:
        residence = RESIDENCE.search(after)
        owner["jati"] = (
            after[: residence.start()] if residence else after
        ).strip() or None
        owner["residence"] = after[residence.start() :].strip() if residence else None
    else:
        residence = RESIDENCE.search(share)
        owner["jati"] = None
        owner["residence"] = share[residence.start() :].strip() if residence else None
        share = share[: residence.start()] if residence else share
    owner["share"] = share.strip() or None
    return owner


def split_info(info: str) -> dict[str, Any]:
    """Split an ``info`` block into area, khata number and owner rows.

    Args:
        info: The free-text block from a plot record.

    Returns:
        ``area_ha`` (float or ``None``), ``khata`` (str or ``None``) and
        ``owners``, one :func:`split_owner` dict per numbered line.
    """
    lines = [line for line in info.split("\n") if line.strip()]
    area = next((AREA.search(line) for line in lines if AREA.search(line)), None)
    khata = next((KHATA.search(line) for line in lines if KHATA.search(line)), None)
    owners = [split_owner(line) for line in lines if OWNER_LINE.match(line)]
    return {
        "area_ha": float(area.group(1)) if area else None,
        "khata": khata.group(1) if khata else None,
        "owners": owners,
    }


def rows_from_record(
    record: dict[str, Any], by_plot: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Owner rows for one fetched record.

    A ``via`` record, one known only from another plot's ``ownerplots``,
    borrows that plot's data from ``by_plot``.

    Args:
        record: One checkpoint line as written by :mod:`rajasthan_ror.plots`.
        by_plot: Plot number to portal data for the same sheet, used to
            resolve ``via`` records.

    Returns:
        One row per co-owner, each carrying the plot's giscode, plot number,
        khata, area, owner count and ``via`` source; empty for a miss.
    """
    if not record.get("ok"):
        return []
    data = record.get("data")
    if data is None and record.get("via") and by_plot:
        data = by_plot.get(record["via"])
    if not data:
        return []
    parsed = split_info(data.get("info") or "")
    base = {
        "giscode": record["giscode"],
        "plotno": record["plotno"],
        "plotid": data.get("plotid"),
        "khata": parsed["khata"],
        "area_ha": parsed["area_ha"],
        "n_owners": len(parsed["owners"]),
        "via": record.get("via"),
    }
    return [{**base, **owner} for owner in parsed["owners"]]


def main() -> None:
    """Command-line entry point: parse every sheet into ``raw/owners.parquet``."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rows: list[dict[str, Any]] = []
    files = sorted(PLOTS_DIR.glob("*.jsonl.gz"))
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh]
        by_plot = {
            record["plotno"]: record["data"]
            for record in records
            if record.get("ok") and record.get("data")
        }
        for record in records:
            rows.extend(rows_from_record(record, by_plot))
    owners = pd.DataFrame(rows)
    owners.to_parquet(OWNERS_FILE, index=False)
    log.info("%d sheets, %d owner rows -> %s", len(files), len(owners), OWNERS_FILE)
    if len(owners):
        log.info("jati filled: %.1f%%", 100 * owners["jati"].notna().mean())
        log.info("relation filled: %.1f%%", 100 * owners["relation"].notna().mean())
        log.info("tenure values: %s", Counter(owners["tenure"]).most_common(8))
        log.info("top jati: %s", Counter(owners["jati"].dropna()).most_common(25))


if __name__ == "__main__":
    main()
