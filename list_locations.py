"""Enumerate the portal's location tree down to the sheet, one row per sheet.

Six levels: district, tehsil, RI circle, halka, village, sheet. The sheet is
the unit ``getPlotInfo`` is keyed on, so it is the unit the crawl is keyed on.
Villages typically have one sheet; a village listed twice, as ``(गत)`` past
and ``(चालु)`` current, carries one code and is folded to the current row.

Checkpointed per district as gzipped JSONL under ``raw/locations/`` so an
interrupted run resumes at the district it was in, and folded into
``raw/villages.parquet`` at the end.

Usage:
    uv run python list_locations.py [--districts 01,21] [--workers 4]
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import queue
import threading
from pathlib import Path

import pandas as pd

from bhunaksha import LEVEL_LABELS, LEVELS, Session, giscode

HERE = Path(__file__).resolve().parent
LOCATIONS = HERE / "raw" / "locations"
VILLAGES = HERE / "raw" / "villages.parquet"

log = logging.getLogger("list_locations")


def walk(session: Session, codes: list[str], names: list[str], out: list[dict]) -> None:
    for child in session.children(codes):
        c, n = codes + [child.code], names + [child.name]
        if len(c) == LEVELS:
            row = {f"{lbl}_code": v for lbl, v in zip(LEVEL_LABELS, c)}
            row.update({f"{lbl}_name": v for lbl, v in zip(LEVEL_LABELS, n)})
            row["giscode"] = giscode(c)
            row["has_data"] = child.has_data
            out.append(row)
        else:
            walk(session, c, n, out)


def district_file(code: str) -> Path:
    return LOCATIONS / f"district_{code}.jsonl.gz"


def do_district(code: str, name: str) -> int:
    path = district_file(code)
    if path.exists():
        log.info("district %s %s: already listed", code, name)
        return 0
    session = Session()
    rows: list[dict] = []
    walk(session, [code], [name], rows)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.rename(path)
    log.info("district %s %s: %d sheets", code, name, len(rows))
    return len(rows)


def fold() -> pd.DataFrame:
    frames = []
    for p in sorted(LOCATIONS.glob("district_*.jsonl.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            frames.append(pd.DataFrame([json.loads(line) for line in fh]))
    df = pd.concat(frames, ignore_index=True)
    # A village the portal lists twice, as "(गत)" past and "(चालु)" current,
    # carries one code and so one giscode; the current row is kept.
    df = df.sort_values("village_name", key=lambda s: ~s.str.contains("चालु", na=False))
    dupes = df["giscode"].duplicated()
    if dupes.any():
        log.info("%d sheets listed twice (past/current village pair); keeping current", dupes.sum())
    df = df[~dupes].sort_values(["district_code", "tehsil_code", "village_code", "sheet_code"])
    df.to_parquet(VILLAGES, index=False)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--districts", help="comma-separated district codes; default all")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    LOCATIONS.mkdir(parents=True, exist_ok=True)

    districts = Session().children([])
    if args.districts:
        keep = set(args.districts.split(","))
        districts = [d for d in districts if d.code in keep]
    log.info("%d districts", len(districts))

    q: queue.Queue = queue.Queue()
    for d in districts:
        q.put(d)

    def worker() -> None:
        while True:
            try:
                d = q.get_nowait()
            except queue.Empty:
                return
            try:
                do_district(d.code, d.name)
            except Exception:  # noqa: BLE001 - one district failing must not stop the rest
                log.exception("district %s failed", d.code)
            finally:
                q.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    df = fold()
    log.info(
        "%d sheets, %d villages, %d districts -> %s",
        len(df),
        df.groupby(["district_code", "tehsil_code", "village_code"]).ngroups,
        df["district_code"].nunique(),
        VILLAGES,
    )


if __name__ == "__main__":
    main()
