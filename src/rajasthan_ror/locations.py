"""Enumerate the portal's location tree down to the sheet, one row per sheet.

Six levels: district, tehsil, RI circle, halka, village, sheet. The sheet is
the unit ``getPlotInfo`` is keyed on, so it is the unit the crawl is keyed on.
Villages typically have one sheet; a village listed twice, as ``(गत)`` past
and ``(चालु)`` current, carries one code and is folded to the current row.

Checkpointed per district as gzipped JSONL under ``raw/locations/`` so an
interrupted run resumes at the district it was in, and folded into
``raw/villages.parquet`` at the end. ``raw/`` is under the current working
directory; see :mod:`rajasthan_ror.paths`.

Usage:
    uv run rajasthan-ror-list [--districts 01,21] [--workers 4]
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import queue
import threading
from typing import TYPE_CHECKING, Any

import pandas as pd

from rajasthan_ror.paths import LOCATIONS_DIR, VILLAGES_FILE
from rajasthan_ror.portal import LEVEL_LABELS, LEVELS, Level, Session, giscode

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)


def walk(
    session: Session, codes: list[str], names: list[str], sheets: list[dict[str, Any]]
) -> None:
    """Descend from ``codes`` to the sheets beneath it, appending one row each.

    Args:
        session: Portal session to query.
        codes: Level codes of the entry to descend from.
        names: Level names matching ``codes``.
        sheets: Output list; a row per sheet carries every level's code and
            name, the giscode and the ``has_data`` flag.
    """
    for child in session.children(codes):
        child_codes, child_names = [*codes, child.code], [*names, child.name]
        if len(child_codes) == LEVELS:
            row: dict[str, Any] = {
                f"{label}_code": value
                for label, value in zip(LEVEL_LABELS, child_codes, strict=True)
            }
            row.update(
                {
                    f"{label}_name": value
                    for label, value in zip(LEVEL_LABELS, child_names, strict=True)
                }
            )
            row["giscode"] = giscode(child_codes)
            row["has_data"] = child.has_data
            sheets.append(row)
        else:
            walk(session, child_codes, child_names, sheets)


def district_file(code: str) -> Path:
    """Checkpoint file for one district.

    Args:
        code: District code as the portal gives it.

    Returns:
        Path of the gzipped JSONL under ``raw/locations/``.
    """
    return LOCATIONS_DIR / f"district_{code}.jsonl.gz"


def list_district(code: str, name: str) -> int:
    """List every sheet in one district and checkpoint the result.

    Args:
        code: District code.
        name: District name, for the log and the rows.

    Returns:
        Number of sheets written; zero when the district was already listed.
    """
    path = district_file(code)
    if path.exists():
        log.info("district %s %s: already listed", code, name)
        return 0
    session = Session()
    sheets: list[dict[str, Any]] = []
    walk(session, [code], [name], sheets)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for row in sheets:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.rename(path)
    log.info("district %s %s: %d sheets", code, name, len(sheets))
    return len(sheets)


def fold() -> pd.DataFrame:
    """Combine the district checkpoints into ``raw/villages.parquet``.

    Returns:
        One row per sheet, past/current village pairs folded to the
        current row.
    """
    frames = []
    for path in sorted(LOCATIONS_DIR.glob("district_*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            frames.append(pd.DataFrame([json.loads(line) for line in fh]))
    villages = pd.concat(frames, ignore_index=True)
    # A village the portal lists twice, as "(गत)" past and "(चालु)" current,
    # carries one code and so one giscode; the current row is kept.
    villages = villages.sort_values(
        "village_name", key=lambda names: ~names.str.contains("चालु", na=False)
    )
    duplicated = int(villages["giscode"].duplicated().sum())
    if duplicated:
        log.info(
            "%d sheets listed twice (past/current village pair); keeping current",
            duplicated,
        )
    villages = villages.drop_duplicates("giscode").sort_values(
        ["district_code", "tehsil_code", "village_code", "sheet_code"]
    )
    villages.to_parquet(VILLAGES_FILE, index=False)
    return villages


def main() -> None:
    """Command-line entry point: list districts in parallel, then fold."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--districts", help="comma-separated district codes; default all"
    )
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    LOCATIONS_DIR.mkdir(parents=True, exist_ok=True)

    districts = Session().children([])
    if args.districts:
        keep = set(args.districts.split(","))
        districts = [district for district in districts if district.code in keep]
    log.info("%d districts", len(districts))

    pending: queue.Queue[Level] = queue.Queue()
    for district in districts:
        pending.put(district)

    def worker() -> None:
        while True:
            try:
                district = pending.get_nowait()
            except queue.Empty:
                return
            try:
                list_district(district.code, district.name)
            except Exception:  # one district failing must not stop the rest
                log.exception("district %s failed", district.code)
            finally:
                pending.task_done()

    threads = [
        threading.Thread(target=worker, daemon=True) for _ in range(args.workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    villages = fold()
    log.info(
        "%d sheets, %d villages, %d districts -> %s",
        len(villages),
        villages.groupby(["district_code", "tehsil_code", "village_code"]).ngroups,
        villages["district_code"].nunique(),
        VILLAGES_FILE,
    )


if __name__ == "__main__":
    main()
