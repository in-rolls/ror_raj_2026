"""Integrity checks and backed-up checkpoint repair."""

import gzip
import json
import os
import runpy
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
inspect_sheet = runpy.run_path(str(SCRIPTS / "crawl_health.py"))["inspect_sheet"]
normalize = runpy.run_path(str(SCRIPTS / "repair_checkpoints.py"))["normalize"]


def row(plot, **kwargs):
    return {
        "giscode": "g",
        "plotno": str(plot),
        "ok": True,
        "fetched_at": "2026-09-11T00:00:00+00:00",
        **kwargs,
    }


def test_repairs_reference_deduplicates_and_reopens():
    source = row(1, data={"ownerplots": ["1", "2"], "info": ""})
    saved = [
        source,
        source,
        row(2, via=None),
        row(3, via=None),
        {"giscode": "g", "done": True},
    ]
    repaired = normalize(saved)
    assert repaired == [source, row(2, via="1")]
    assert saved[2]["via"] is None
    assert saved[-1]["done"]


def test_repair_prefers_direct_answer_over_inference_and_misses():
    source = row(1, data={"ownerplots": ["1", "2"]})
    target = row(2, data={"ownerplots": ["2"]})
    assert normalize(
        [source, target, row(2, via="1"), row(2, ok=False, reason="miss")]
    ) == [source, target]


def test_audit_finds_unresolved_reference_and_numbering_gap(tmp_path):
    path = tmp_path / "g.jsonl.gz"
    with gzip.open(path, "wt") as stream:
        for record in [
            row(1, data={"ownerplots": ["1"]}),
            row(3, via=None),
            {"giscode": "g", "done": True},
        ]:
            stream.write(json.dumps(record) + "\n")
    result = inspect_sheet(path, {"g"})
    assert result["issues"]["unresolved_via"] == 1
    assert result["issues"]["unvisited_numbers_before_last_hit"] == 1


def test_audit_distinguishes_open_stream_from_old_truncation(tmp_path):
    path = tmp_path / "g.jsonl.gz"
    with gzip.open(path, "wt") as stream:
        stream.write(json.dumps(row(1, data={})) + "\n")
    path.write_bytes(path.read_bytes()[:-8])
    result = inspect_sheet(path, {"g"})
    assert result["counts"]["active_open_tail"] == 1
    assert "unreadable_tail" not in result["issues"]
    old = time.time() - 3600
    os.utime(path, (old, old))
    assert inspect_sheet(path, {"g"})["issues"]["unreadable_tail"] == 1


def test_census_parses_records_beyond_twentieth(tmp_path):
    path = tmp_path / "g.jsonl.gz"
    with gzip.open(path, "wt") as stream:
        for plot in range(1, 26):
            stream.write(
                json.dumps(
                    row(plot, data={"info": "unparsed", "ownerplots": [str(plot)]})
                )
                + "\n"
            )
    counts = inspect_sheet(path, {"g"})["counts"]
    assert counts["audited_direct_records"] == counts["direct"] == 25
    assert counts["audited_records_without_owners"] == 25
