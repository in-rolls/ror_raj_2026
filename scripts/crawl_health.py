"""Audit crawl checkpoints and save aggregate health reports without changing data."""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import time
import zlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from rajasthan_ror.parse import split_info
from rajasthan_ror.plots import integer_plots


def inspect_sheet(path: Path, expected: set[str]) -> dict:
    code = path.name.removesuffix(".jsonl.gz")
    before = path.stat()
    counts: Counter = Counter()
    issues: Counter = Counter()
    sources = {}
    references = {}
    answered = set()
    successful = set()
    done = False
    last = {}
    latest = ""
    if code not in expected:
        issues["sheet_not_in_frame"] += 1
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                record = json.loads(line)
                last = record
                if record.get("giscode") != code:
                    issues["wrong_sheet_key"] += 1
                if record.get("done"):
                    done = True
                    continue
                if done:
                    issues["records_after_done"] += 1
                plot = record.get("plotno", "")
                if not isinstance(plot, str) or not plot.isdigit():
                    issues["invalid_plot_key"] += 1
                    continue
                stamp = record.get("fetched_at", "")
                if not stamp:
                    issues["missing_timestamp"] += 1
                latest = max(latest, stamp)
                if record.get("ok") or record.get("reason") == "miss":
                    if plot in answered:
                        issues["duplicate_answer"] += 1
                    answered.add(plot)
                if not record.get("ok"):
                    kind = "misses" if record.get("reason") == "miss" else "failures"
                    counts[kind] += 1
                    continue
                successful.add(plot)
                counts["plots"] += 1
                data = record.get("data")
                if data is None:
                    counts["inferred"] += 1
                    references[plot] = record.get("via")
                    continue
                counts["direct"] += 1
                if not isinstance(data, dict):
                    issues["invalid_payload"] += 1
                    continue
                sources[plot] = integer_plots(data.get("ownerplots"))
                info = data.get("info")
                if not isinstance(info, str) or not info.strip():
                    counts["empty_info"] += 1
                    continue
                counts["audited_direct_records"] += 1
                try:
                    owners = split_info(info)["owners"]
                    counts["audited_records_without_owners"] += not owners
                    for owner in owners:
                        counts["audited_owners"] += 1
                        for field in (
                            "name",
                            "relation",
                            "relative",
                            "jati",
                            "share",
                        ):
                            counts[f"audited_{field}_filled"] += bool(owner[field])
                except (ValueError, TypeError):
                    issues["parse_error"] += 1
    except (EOFError, OSError, zlib.error, json.JSONDecodeError, UnicodeError):
        issues["unreadable_tail"] += 1
    after = path.stat()
    active = (before.st_size, before.st_mtime_ns) != (
        after.st_size,
        after.st_mtime_ns,
    ) or time.time() - after.st_mtime < 300
    if active and issues["unreadable_tail"]:
        counts["active_open_tail"] += issues.pop("unreadable_tail")
    for plot, via in references.items():
        if via not in sources:
            issues["unresolved_via"] += 1
        elif plot not in sources[via]:
            issues["via_not_in_ownerplots"] += 1
    if done and not last.get("done"):
        issues["done_not_last"] += 1
    if done and successful:
        holes = set(range(1, max(map(int, successful)) + 1)) - set(map(int, answered))
        issues["unvisited_numbers_before_last_hit"] += len(holes)
    return {
        "done": done and bool(last.get("done")) and not issues["unreadable_tail"],
        "active": active,
        "latest_record": latest,
        "counts": dict(counts),
        "issues": {key: value for key, value in issues.items() if value},
    }


def main() -> None:
    started = time.monotonic()
    frame = pd.read_parquet("raw/villages.parquet")
    frame = frame[frame.has_data]
    expected = set(frame.giscode)
    counts: Counter = Counter()
    issues: Counter = Counter()
    done = set()
    problems = {}
    newest = 0.0
    latest = ""
    files = list(Path("raw/plots").glob("*.jsonl.gz"))
    for path in files:
        result = inspect_sheet(path, expected)
        code = path.name.removesuffix(".jsonl.gz")
        if result["done"]:
            done.add(code)
        counts.update(result["counts"])
        issues.update(result["issues"])
        if result["issues"]:
            problems[code] = result["issues"]
        newest = max(newest, path.stat().st_mtime)
        latest = max(latest, result["latest_record"])
    report_path = Path("logs/crawl-health.json")
    previous = json.loads(report_path.read_text()) if report_path.exists() else {}
    report = {
        "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "eligible_sheets": len(expected),
        "duplicate_frame_keys": len(frame) - len(expected),
        "completed_sheets": len(done & expected),
        "checkpoint_files": len(files),
        "counts": dict(counts),
        "issues": dict(issues),
        "problem_sheets": problems,
        "latest_record": latest,
        "seconds_since_checkpoint_write": round(time.time() - newest),
        "free_disk_gb": round(shutil.disk_usage(".").free / 1e9, 2),
        "data_free_disk_gb": round(shutil.disk_usage("raw").free / 1e9, 2),
        "audit_seconds": round(time.monotonic() - started, 1),
        "parse_scope": "all saved direct payloads; every inferred reference checked",
        "audit_mode": "census",
        "districts": {
            code: {"total": len(group), "done": int(group.giscode.isin(done).sum())}
            for code, group in frame.groupby("district_code")
        },
    }
    report["new_completed_since_previous_audit"] = (
        report["completed_sheets"] - previous["completed_sheets"] if previous else None
    )
    report["new_plots_since_previous_audit"] = (
        counts["plots"] - previous["counts"]["plots"] if previous else None
    )
    report["warnings"] = []
    if issues:
        report["warnings"].append(f"Checkpoint integrity issues: {dict(issues)}")
    if report["seconds_since_checkpoint_write"] > 1800:
        report["warnings"].append("No checkpoint writes for at least 30 minutes")
    if previous and report["new_plots_since_previous_audit"] == 0:
        report["warnings"].append("No new plots since previous audit")
    if min(report["free_disk_gb"], report["data_free_disk_gb"]) < 5:
        report["warnings"].append("Less than 5 GB disk space available")
    report_path.parent.mkdir(exist_ok=True)
    temporary = report_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(report_path)
    summary = {key: value for key, value in report.items() if key != "problem_sheets"}
    with Path("logs/crawl-health-history.jsonl").open("a") as stream:
        stream.write(json.dumps(summary) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", action="store_true", help="audit every 30 minutes")
    args = parser.parse_args()
    while True:
        main()
        if not args.watch:
            break
        time.sleep(1800)
