"""Fetch every plot record for every sheet in ``raw/villages.parquet``.

The portal has no call that lists the plots on a sheet; the map finds them by
click. So the crawl walks plot numbers upward from 1. Khasra numbers are
dense integers, but subdivided khasras are written ``1256/287`` on the nakal
and the portal has not answered to that form under any spelling tried, so
what this collects is the integer-numbered plots. The parse records which
numbers were tried.

Where a sheet ends is found by probing, not by a long run of misses. Over 68
finished Nagaur sheets the largest gap inside a sheet's numbering had median
3 and maximum 57, so a plain miss run had to be 60 to be safe and cost 60
requests on every sheet, 16% of all requests. Instead, after ``--probe-after``
consecutive misses the crawl probes ahead at doubling offsets (20, 40, 80,
160, 320, 640 past the current number). A hit resumes the walk; six misses
end the sheet. That is 26 requests where 60 were, and it reaches 660 numbers
past the last hit where the old rule reached 60.

Every hit carries ``ownerplots``, the other plot numbers on the same khata.
Those share the owner block by construction, so they are recorded as
``via`` the plot that named them rather than fetched, which cuts the request
count from the number of plots to roughly the number of khatas plus the
misses. The parse expands them back to one row per plot.

Checkpointing is per sheet, appended to ``raw/plots/<giscode>.jsonl.gz``, one
line per plot number tried: ``ok`` true with the portal's JSON, ``ok`` false
with a reason. A sheet whose file ends with a ``done`` line is skipped on the
next run; one that does not replays saved answers and fetches missing numbers.
``raw/`` is under the current working directory; see
:mod:`rajasthan_ror.paths`.

Usage:
    uv run rajasthan-ror-fetch --districts 21 --workers 8
    uv run rajasthan-ror-fetch --giscodes 0100207450292011035001
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import json
import logging
import queue
import shutil
import threading
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from rajasthan_ror.paths import PLOTS_DIR, VILLAGES_FILE
from rajasthan_ror.portal import PortalError, Session

log = logging.getLogger(__name__)

PROBE_OFFSETS = (20, 40, 80, 160, 320, 640)


def sheet_file(giscode: str) -> Path:
    """Checkpoint file for one sheet.

    Args:
        giscode: The sheet's giscode.

    Returns:
        Path of the gzipped JSONL under ``raw/plots/``.
    """
    return PLOTS_DIR / f"{giscode}.jsonl.gz"


def trim_truncated(path: Path) -> int:
    """Cut a checkpoint with a corrupt tail back to its readable prefix.

    gzip readers stop at the first damaged member, so appending to a file a
    crash cut short would hide every later record from the parser. The
    original bytes are backed up before the file is rewritten with its
    readable lines; the crawl then refetches from there.

    Args:
        path: The sheet's checkpoint file; may not exist yet.

    Returns:
        Number of lines kept, or zero when the file was intact or absent.
    """
    if not path.exists():
        return 0
    lines: list[str] = []
    intact = False
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            lines.extend(fh)
        intact = True
    except (EOFError, OSError, zlib.error):
        pass
    if lines:
        try:
            json.loads(lines[-1])
        except json.JSONDecodeError:
            lines.pop()
            intact = False
    if intact:
        return 0
    backup = path.with_name(f"{path.name}.truncated-{time.time_ns()}.bak")
    shutil.copy2(path, backup)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        fh.writelines(lines)
    tmp.replace(path)
    log.warning("%s: truncated checkpoint; kept %d lines", path, len(lines))
    return len(lines)


def read_progress(path: Path) -> tuple[int, bool, int, dict[str, str]]:
    """Recover where a sheet's crawl got to from its checkpoint file.

    Tolerates a truncated tail from a killed worker: the readable prefix is
    used and the rest is refetched. ``trim_truncated`` should run first so
    the tail is not appended after.

    Args:
        path: The sheet's checkpoint file; may not exist yet.

    Returns:
        Highest plot number tried, whether the sheet is finished, the number
        of hits so far, and the plot numbers already known via
        ``ownerplots``, each mapped to the plot that named it.
    """
    highest, done, hits = 0, False, 0
    via: dict[str, str] = {}
    if not path.exists():
        return highest, done, hits, via
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    done = False
                    break
                done = bool(record.get("done"))
                if record.get("done"):
                    continue
                if not record.get("ok") and record.get("reason") != "miss":
                    continue  # a failed request: retry it, do not count it as tried
                highest = max(highest, int(record["plotno"]))
                hits += bool(record.get("ok"))
                if record.get("ok") and "data" in record:
                    named = integer_plots(record["data"].get("ownerplots"))
                    for other in named - {record["plotno"]}:
                        via.setdefault(other, record["plotno"])
    except (EOFError, OSError, zlib.error):
        done = False
        log.warning("%s: unreadable tail; resuming after plot %d", path, highest)
    return highest, done, hits, via


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
    session: Session, giscode: str, probe_after: int, max_plot: int
) -> tuple[int, int]:
    """Walk one sheet's plot numbers upward, probing ahead to find its end.

    A portal failure is checkpointed before it propagates to the caller.

    Args:
        session: Portal session to fetch with.
        giscode: The sheet to crawl.
        probe_after: Consecutive misses after which to probe ahead.
        max_plot: Highest plot number to try.

    Returns:
        Requests made and plots found (including ones already known
        from a previous run).
    """
    path = sheet_file(giscode)
    trim_truncated(path)
    _, done, hits, via = read_progress(path)
    if done:
        return 0, hits
    tried = 0
    known = set(via)
    probed: set[int] = set()
    answered: dict[int, bool] = {}
    if path.exists():
        with gzip.open(path, "rt", encoding="utf-8") as checkpoint:
            for line in checkpoint:
                record = json.loads(line)
                if record.get("ok") or record.get("reason") == "miss":
                    answered[int(record["plotno"])] = bool(record.get("ok"))
    last_hit = max((p for p, ok in answered.items() if ok), default=0)

    with gzip.open(path, "at", encoding="utf-8") as fh:

        def fetch(plot: int) -> bool:
            """Fetch one plot number, checkpoint it, and say whether it exists."""
            nonlocal tried, hits, last_hit
            if plot in answered:
                return answered[plot]
            record: dict[str, Any] = {
                "giscode": giscode,
                "plotno": str(plot),
                "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            }
            if str(plot) in known:
                hits += 1
                record.update(ok=True, via=via.get(str(plot)))
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                answered[plot] = True
                last_hit = max(last_hit, plot)
                return True
            try:
                data = session.plot_info(giscode, str(plot))
            except PortalError as exc:
                record.update(ok=False, reason=str(exc))
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                raise
            tried += 1
            if data is None:
                record.update(ok=False, reason="miss")
            else:
                hits += 1
                last_hit = max(last_hit, plot)
                record.update(ok=True, data=data)
                for other in integer_plots(data.get("ownerplots")) - {str(plot)}:
                    known.add(other)
                    via.setdefault(other, str(plot))
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            fh.flush()
            answered[plot] = data is not None
            return data is not None

        # Replay saved answers: an ahead probe is not a safe resume position.
        plot, misses = 0, 0
        while plot < max_plot:
            plot += 1
            if plot in probed:
                continue
            if fetch(plot):
                misses = 0
                continue
            misses += 1
            if misses < probe_after:
                continue
            # A run of misses: is there anything further on, or is this the end?
            found = None
            for offset in PROBE_OFFSETS:
                if plot + offset > max_plot:
                    break
                probed.add(plot + offset)
                if fetch(plot + offset):
                    found = plot + offset
                    break
            if found is None:
                if plot < last_hit:
                    continue
                break
            misses = 0
        fh.write(json.dumps({"giscode": giscode, "done": True, "high": plot}) + "\n")
    return tried, hits


def run() -> None:
    """Crawl the queued sheets with a worker pool.

    Raises:
        SystemExit: With status 1 if any sheet failed, allowing a supervisor
            to retry the unfinished pass.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--districts", help="comma-separated district codes")
    parser.add_argument(
        "--priority-file", type=Path, help="text file of sheet codes to process first"
    )
    parser.add_argument(
        "--giscodes", help="comma-separated giscodes (overrides --districts)"
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--probe-after",
        type=int,
        default=20,
        help="consecutive misses after which to probe ahead for the sheet's end",
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
    if args.priority_file:
        priority = set(args.priority_file.read_text().split())
        sheets.sort(key=lambda code: code not in priority)
    log.info("%d sheets queued", len(sheets))

    pending: queue.Queue[str] = queue.Queue()
    for giscode in sheets:
        pending.put(giscode)
    totals = {"tried": 0, "hits": 0, "sheets": 0}
    lock = threading.Lock()
    started_at = time.monotonic()

    def worker() -> None:
        session: Session | None = None
        while True:
            try:
                giscode = pending.get_nowait()
            except queue.Empty:
                return
            try:
                if session is None:
                    # Opening a session can itself fail at the connection level;
                    # the back-off inside Session waits that out rather than
                    # letting this thread die, which is what shrank the pool
                    # during the 8 Sept outage.
                    session = Session(pause=args.pause)
                tried, hits = crawl_sheet(
                    session, giscode, args.probe_after, args.max_plot
                )
                with lock:
                    totals["tried"] += tried
                    totals["hits"] += hits
                    totals["sheets"] += 1
                    rate = totals["tried"] / max(time.monotonic() - started_at, 1)
                log.info(
                    "%s: %d tried, %d plots (%.1f req/s overall, %d workers live)",
                    giscode,
                    tried,
                    hits,
                    rate,
                    sum(t.is_alive() for t in threads),
                )
            except Exception:  # a sheet failing must not stop the crawl
                log.exception("sheet %s failed; will resume next run", giscode)
                session = None
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
    if totals["sheets"] != len(sheets):
        log.error(
            "%d sheets unfinished; retry this pass", len(sheets) - totals["sheets"]
        )
        raise SystemExit(1)


def main() -> None:
    """Run one fetcher at a time so checkpoint writers cannot overlap.

    Raises:
        SystemExit: With status 1 for unfinished work or 2 for an active writer.
    """
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    with (PLOTS_DIR / ".crawl.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SystemExit(2) from exc
        run()


if __name__ == "__main__":
    main()
