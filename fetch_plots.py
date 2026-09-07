"""Fetch every plot record for every sheet in ``raw/villages.parquet``.

The portal has no call that lists the plots on a sheet; the map finds them by
click. So the crawl walks plot numbers upward from 1 and stops after
``--miss-run`` consecutive misses. Khasra numbers are dense integers, but
subdivided khasras are written ``1256/287`` on the nakal and the portal has
not answered to that form under any spelling tried, so what this collects is
the integer-numbered plots. The parse records which numbers were tried.

Every hit carries ``ownerplots``, the other plot numbers on the same khata.
Those share the owner block by construction, so they are recorded as
``via`` the plot that named them rather than fetched, which cuts the request
count from the number of plots to roughly the number of khatas plus the
misses. The parse expands them back to one row per plot.

Checkpointing is per sheet, appended to ``raw/plots/<giscode>.jsonl.gz``, one
line per plot number tried: ``ok`` true with the portal's JSON, ``ok`` false
with a reason. A sheet whose file ends with a ``done`` line is skipped on the
next run; one that does not is resumed from the highest plot number tried.

Usage:
    uv run python fetch_plots.py --districts 21 --workers 8 --miss-run 60
    uv run python fetch_plots.py --giscodes 0100207450292011035001
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from bhunaksha import PortalError, Session

HERE = Path(__file__).resolve().parent
VILLAGES = HERE / "raw" / "villages.parquet"
PLOTS = HERE / "raw" / "plots"

log = logging.getLogger("fetch_plots")


def sheet_file(giscode: str) -> Path:
    return PLOTS / f"{giscode}.jsonl.gz"


def read_progress(path: Path) -> tuple[int, bool, int, set[str]]:
    """(highest plot tried, finished?, hits, plots already known via ownerplots).

    Tolerates a truncated tail from a killed worker.
    """
    high, done, hits, known = 0, False, 0, set()
    if not path.exists():
        return high, done, hits, known
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    break
                if rec.get("done"):
                    done = True
                    continue
                high = max(high, int(rec["plotno"]))
                hits += bool(rec.get("ok"))
                if rec.get("ok") and not rec.get("via"):
                    known.update(_int_plots(rec["data"].get("ownerplots")))
    except (EOFError, OSError):
        pass
    return high, done, hits, known


def _int_plots(ownerplots) -> set[str]:
    """Plot numbers the walk could reach, i.e. plain integers. Slashed ones it cannot."""
    if isinstance(ownerplots, str):
        ownerplots = [p.strip(" '") for p in ownerplots.strip("[]").split(",")]
    return {str(p) for p in (ownerplots or []) if str(p).isdigit()}


def crawl_sheet(session: Session, giscode: str, miss_run: int, max_plot: int) -> tuple[int, int]:
    path = sheet_file(giscode)
    start, done, hits, known = read_progress(path)
    if done:
        return 0, hits
    misses, tried = 0, 0
    via: dict[str, str] = {}
    with gzip.open(path, "at", encoding="utf-8") as fh:
        plot = start
        while misses < miss_run and plot < max_plot:
            plot += 1
            rec: dict = {
                "giscode": giscode,
                "plotno": str(plot),
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            if str(plot) in known:
                misses = 0
                hits += 1
                rec.update(ok=True, via=via.get(str(plot)))
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                continue
            try:
                data = session.plot_info(giscode, str(plot))
            except PortalError as e:
                rec.update(ok=False, reason=str(e))
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                raise
            tried += 1
            if data is None:
                misses += 1
                rec.update(ok=False, reason="miss")
            else:
                misses = 0
                hits += 1
                rec.update(ok=True, data=data)
                for other in _int_plots(data.get("ownerplots")) - {str(plot)}:
                    known.add(other)
                    via.setdefault(other, str(plot))
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
        fh.write(json.dumps({"giscode": giscode, "done": True, "high": plot}) + "\n")
    return tried, hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--districts", help="comma-separated district codes")
    ap.add_argument("--giscodes", help="comma-separated giscodes (overrides --districts)")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--miss-run", type=int, default=60, help="consecutive misses that end a sheet")
    ap.add_argument("--max-plot", type=int, default=20000)
    ap.add_argument("--pause", type=float, default=0.2, help="seconds between requests per worker")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    PLOTS.mkdir(parents=True, exist_ok=True)

    if args.giscodes:
        sheets = args.giscodes.split(",")
    else:
        df = pd.read_parquet(VILLAGES)
        if args.districts:
            df = df[df["district_code"].isin(args.districts.split(","))]
        sheets = df.loc[df["has_data"], "giscode"].tolist()
    log.info("%d sheets queued", len(sheets))

    q: queue.Queue = queue.Queue()
    for g in sheets:
        q.put(g)
    totals = {"tried": 0, "hits": 0, "sheets": 0}
    lock = threading.Lock()
    t0 = time.monotonic()

    def worker() -> None:
        session = Session(pause=args.pause)
        while True:
            try:
                g = q.get_nowait()
            except queue.Empty:
                return
            try:
                tried, hits = crawl_sheet(session, g, args.miss_run, args.max_plot)
                with lock:
                    totals["tried"] += tried
                    totals["hits"] += hits
                    totals["sheets"] += 1
                    rate = totals["tried"] / max(time.monotonic() - t0, 1)
                log.info("%s: %d tried, %d plots (%.1f req/s overall)", g, tried, hits, rate)
            except Exception:  # noqa: BLE001 - a sheet failing must not stop the crawl
                log.exception("sheet %s failed; will resume next run", g)
                session = Session(pause=args.pause)
            finally:
                q.task_done()

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    log.info("done: %(sheets)d sheets, %(tried)d requests, %(hits)d plots", totals)


if __name__ == "__main__":
    main()
