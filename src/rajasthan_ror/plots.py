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
``raw/`` is under the current working directory; see
:mod:`rajasthan_ror.paths`.

Usage:
    uv run rajasthan-ror-fetch --districts 21 --workers 8 --miss-run 60
    uv run rajasthan-ror-fetch --giscodes 0100207450292011035001
"""

from __future__ import annotations

import argparse
import gzip
import json
import logging
import queue
import threading
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pandas as pd

from rajasthan_ror.paths import PLOTS_DIR, VILLAGES_FILE
from rajasthan_ror.portal import PortalError, Session

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)


def sheet_file(giscode: str) -> Path:
    """Checkpoint file for one sheet.

    Args:
        giscode: The sheet's giscode.

    Returns:
        Path of the gzipped JSONL under ``raw/plots/``.
    """
    return PLOTS_DIR / f"{giscode}.jsonl.gz"


def read_progress(path: Path) -> tuple[int, bool, int, set[str]]:
    """Recover where a sheet's crawl got to from its checkpoint file.

    Tolerates a truncated tail from a killed worker: the readable prefix is
    used and the rest is refetched.

    Args:
        path: The sheet's checkpoint file; may not exist yet.

    Returns:
        Highest plot number tried, whether the sheet is finished, the number
        of hits so far, and the plot numbers already known via
        ``ownerplots``.
    """
    highest, done, hits, known = 0, False, 0, set()
    if not path.exists():
        return highest, done, hits, known
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    break
                if record.get("done"):
                    done = True
                    continue
                highest = max(highest, int(record["plotno"]))
                hits += bool(record.get("ok"))
                if record.get("ok") and not record.get("via"):
                    known.update(integer_plots(record["data"].get("ownerplots")))
    except (EOFError, OSError):
        log.warning("%s: truncated checkpoint; resuming after plot %d", path, highest)
    return highest, done, hits, known


def integer_plots(ownerplots: str | list[Any] | None) -> set[str]:
    """Plot numbers the walk could reach, i.e. plain integers.

    Slashed (subdivided) numbers are dropped: the portal does not answer to
    them. The portal sends ``ownerplots`` sometimes as a list and sometimes
    as its string repr, so both shapes are read.

    Args:
        ownerplots: The ``ownerplots`` field of a plot record.

    Returns:
        The integer plot numbers, as strings.
    """
    if isinstance(ownerplots, str):
        ownerplots = [p.strip(" '") for p in ownerplots.strip("[]").split(",")]
    return {str(p) for p in (ownerplots or []) if str(p).isdigit()}


def crawl_sheet(
    session: Session, giscode: str, miss_run: int, max_plot: int
) -> tuple[int, int]:
    """Walk one sheet's plot numbers upward until a run of misses.

    Args:
        session: Portal session to fetch with.
        giscode: The sheet to crawl.
        miss_run: Consecutive misses that end the sheet.
        max_plot: Highest plot number to try.

    Returns:
        Requests made and plots found (including ones already known
        from a previous run).

    Raises:
        PortalError: When a request fails after retries; the failure is
            checkpointed first so the next run resumes after it.
    """
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
            record: dict[str, Any] = {
                "giscode": giscode,
                "plotno": str(plot),
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            if str(plot) in known:
                misses = 0
                hits += 1
                record.update(ok=True, via=via.get(str(plot)))
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                continue
            try:
                data = session.plot_info(giscode, str(plot))
            except PortalError as exc:
                record.update(ok=False, reason=str(exc))
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                raise
            tried += 1
            if data is None:
                misses += 1
                record.update(ok=False, reason="miss")
            else:
                misses = 0
                hits += 1
                record.update(ok=True, data=data)
                for other in integer_plots(data.get("ownerplots")) - {str(plot)}:
                    known.add(other)
                    via.setdefault(other, str(plot))
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
        fh.write(json.dumps({"giscode": giscode, "done": True, "high": plot}) + "\n")
    return tried, hits


def main() -> None:
    """Command-line entry point: crawl the queued sheets with a worker pool."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--districts", help="comma-separated district codes")
    parser.add_argument(
        "--giscodes", help="comma-separated giscodes (overrides --districts)"
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--miss-run", type=int, default=60, help="consecutive misses that end a sheet"
    )
    parser.add_argument("--max-plot", type=int, default=20000)
    parser.add_argument(
        "--pause", type=float, default=0.2, help="seconds between requests per worker"
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.giscodes:
        sheets = args.giscodes.split(",")
    else:
        villages = pd.read_parquet(VILLAGES_FILE)
        if args.districts:
            villages = villages[
                villages["district_code"].isin(args.districts.split(","))
            ]
        sheets = villages.loc[villages["has_data"], "giscode"].tolist()
    log.info("%d sheets queued", len(sheets))

    pending: queue.Queue[str] = queue.Queue()
    for giscode in sheets:
        pending.put(giscode)
    totals = {"tried": 0, "hits": 0, "sheets": 0}
    lock = threading.Lock()
    started_at = time.monotonic()

    def worker() -> None:
        session = Session(pause=args.pause)
        while True:
            try:
                giscode = pending.get_nowait()
            except queue.Empty:
                return
            try:
                tried, hits = crawl_sheet(
                    session, giscode, args.miss_run, args.max_plot
                )
                with lock:
                    totals["tried"] += tried
                    totals["hits"] += hits
                    totals["sheets"] += 1
                    rate = totals["tried"] / max(time.monotonic() - started_at, 1)
                log.info(
                    "%s: %d tried, %d plots (%.1f req/s overall)",
                    giscode,
                    tried,
                    hits,
                    rate,
                )
            except Exception:  # a sheet failing must not stop the crawl
                log.exception("sheet %s failed; will resume next run", giscode)
                session = Session(pause=args.pause)
            finally:
                pending.task_done()

    threads = [
        threading.Thread(target=worker, daemon=True) for _ in range(args.workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    log.info("done: %(sheets)d sheets, %(tried)d requests, %(hits)d plots", totals)


if __name__ == "__main__":
    main()
