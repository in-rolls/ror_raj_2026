"""Back up and reopen audited checkpoints for a lossless crawl retry."""

from __future__ import annotations

import fcntl
import gzip
import json
import shutil
import zlib
from datetime import UTC, datetime
from pathlib import Path

from rajasthan_ror.plots import integer_plots


def normalize(records: list[dict]) -> list[dict]:
    sources = {
        record["plotno"]: record["data"]
        for record in records
        if record.get("ok") and isinstance(record.get("data"), dict)
    }
    named_by = {}
    for plot, data in sources.items():
        for other in integer_plots(data.get("ownerplots")) - {plot}:
            named_by.setdefault(other, plot)
    answers = {}
    failures = []
    for original in records:
        if original.get("done"):
            continue
        record = dict(original)
        plot = record["plotno"]
        if record.get("ok") and "data" not in record:
            via = record.get("via")
            if via not in sources or plot not in integer_plots(
                sources[via].get("ownerplots")
            ):
                via = named_by.get(plot)
            if via is None:
                continue
            record["via"] = via
        if not record.get("ok") and record.get("reason") != "miss":
            failures.append(record)
            continue
        previous = answers.get(plot)
        rank = (bool(record.get("ok")), "data" in record)
        if previous is None or rank >= (bool(previous.get("ok")), "data" in previous):
            answers[plot] = record
    return failures + list(answers.values())


def main() -> None:
    report = json.loads(Path("logs/crawl-health.json").read_text())
    repairable = {
        "duplicate_answer",
        "records_after_done",
        "unresolved_via",
        "via_not_in_ownerplots",
        "done_not_last",
        "unvisited_numbers_before_last_hit",
        "unreadable_tail",
    }
    codes = sorted(
        code
        for code, issues in report["problem_sheets"].items()
        if repairable.intersection(issues)
    )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = Path("raw/checkpoint-backups") / stamp
    with Path("raw/plots/.crawl.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backup.mkdir(parents=True)
        shutil.copy2("logs/crawl-health.json", backup / "audit-before.json")
        changes = []
        for code in codes:
            path = Path("raw/plots") / f"{code}.jsonl.gz"
            shutil.copy2(path, backup / path.name)
            records = []
            try:
                with gzip.open(path, "rt", encoding="utf-8") as stream:
                    records.extend(json.loads(line) for line in stream)
            except (EOFError, OSError, zlib.error, json.JSONDecodeError):
                pass
            normalized = normalize(records)
            temporary = path.with_suffix(".repair.tmp")
            with gzip.open(temporary, "wt", encoding="utf-8") as stream:
                for record in normalized:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            temporary.replace(path)
            changes.append(
                {"giscode": code, "before": len(records), "after": len(normalized)}
            )
        queue = Path("raw/repair-sheets.txt")
        prior = queue.read_text().split() if queue.exists() else []
        queue.write_text("\n".join(sorted(set(prior) | set(codes))) + "\n")
        (backup / "repairs.json").write_text(json.dumps(changes, indent=2) + "\n")
    print(f"Reopened {len(codes)} sheets; originals preserved in {backup}")


if __name__ == "__main__":
    main()
