"""The sheet walk: probe-ahead ending, ownerplots skipping, resume."""

from __future__ import annotations

import gzip
import json
from typing import TYPE_CHECKING

import pytest

from rajasthan_ror import plots
from rajasthan_ror.plots import crawl_sheet, read_progress
from rajasthan_ror.portal import PortalError

if TYPE_CHECKING:
    from pathlib import Path


class FakeSession:
    """Answers plot_info from a fixed set of existing plot numbers."""

    def __init__(self, existing: set[int], fail_at: int | None = None) -> None:
        self.existing = existing
        self.fail_at = fail_at
        self.asked: list[int] = []

    def plot_info(self, giscode: str, plotno: str) -> dict[str, object] | None:
        plot = int(plotno)
        self.asked.append(plot)
        if plot == self.fail_at:
            raise PortalError("boom")
        if plot not in self.existing:
            return None
        return {"info": f"plot {plot}", "ownerplots": [str(plot)]}


def records(path: Path) -> list[dict[str, object]]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


@pytest.fixture
def plots_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(plots, "PLOTS_DIR", tmp_path)
    return tmp_path


def test_dense_sheet_ends_after_one_probe_series(plots_dir: Path) -> None:
    session = FakeSession(set(range(1, 101)))
    tried, hits = crawl_sheet(session, "g", probe_after=20, max_plot=5000)  # type: ignore[arg-type]
    assert hits == 100
    # 100 hits, 20 misses, then six probes that all miss
    assert tried == 100 + 20 + len(plots.PROBE_OFFSETS)
    assert max(session.asked) == 120 + 640
    assert records(plots_dir / "g.jsonl.gz")[-1]["done"] is True


def test_gap_of_57_is_crossed(plots_dir: Path) -> None:
    existing = set(range(1, 51)) | set(range(108, 160))
    session = FakeSession(existing)
    _, hits = crawl_sheet(session, "g", probe_after=20, max_plot=5000)  # type: ignore[arg-type]
    assert hits == len(existing)


def test_sparse_tail_beyond_reach_is_missed_knowingly(plots_dir: Path) -> None:
    # One plot 700 numbers past the last hit: no probe lands on it.
    session = FakeSession(set(range(1, 11)) | {710})
    _, hits = crawl_sheet(session, "g", probe_after=20, max_plot=5000)  # type: ignore[arg-type]
    assert hits == 10


def test_single_plot_and_empty_sheet(plots_dir: Path) -> None:
    _, hits = crawl_sheet(FakeSession({1}), "one", probe_after=20, max_plot=5000)  # type: ignore[arg-type]
    assert hits == 1
    tried, hits = crawl_sheet(FakeSession(set()), "none", probe_after=20, max_plot=5000)  # type: ignore[arg-type]
    assert (tried, hits) == (20 + len(plots.PROBE_OFFSETS), 0)


def test_ownerplots_are_not_fetched(plots_dir: Path) -> None:
    class Owner(FakeSession):
        def plot_info(self, giscode: str, plotno: str) -> dict[str, object] | None:
            got = super().plot_info(giscode, plotno)
            if got is not None:
                got["ownerplots"] = [str(p) for p in sorted(self.existing)]
            return got

    session = Owner(set(range(1, 31)))
    _, hits = crawl_sheet(session, "g", probe_after=20, max_plot=5000)  # type: ignore[arg-type]
    assert hits == 30
    assert 1 in session.asked
    assert 2 not in session.asked
    assert (
        sum(r.get("via") is not None for r in records(plots_dir / "g.jsonl.gz")) == 29
    )


def test_failure_is_checkpointed_and_resumed(plots_dir: Path) -> None:
    existing = set(range(1, 41))
    with pytest.raises(PortalError):
        crawl_sheet(
            FakeSession(existing, fail_at=25), "g", probe_after=20, max_plot=5000
        )  # type: ignore[arg-type]
    highest, done, hits, _ = read_progress(plots_dir / "g.jsonl.gz")
    assert (highest, done, hits) == (24, False, 24)
    second = FakeSession(existing)
    _, hits = crawl_sheet(second, "g", probe_after=20, max_plot=5000)  # type: ignore[arg-type]
    assert min(second.asked) == 25
    assert hits == 40
    assert read_progress(plots_dir / "g.jsonl.gz")[1] is True


def test_resumed_run_records_where_known_plots_came_from(plots_dir: Path) -> None:
    """A plot learnt from ownerplots before the crash is still attributed after it."""
    existing = set(range(1, 41))
    with pytest.raises(PortalError):
        crawl_sheet(
            FakeSession(existing, fail_at=25), "g", probe_after=20, max_plot=5000
        )  # type: ignore[arg-type]
    crawl_sheet(FakeSession(existing), "g", probe_after=20, max_plot=5000)  # type: ignore[arg-type]
    for record in records(plots_dir / "g.jsonl.gz"):
        if record.get("ok"):
            assert "data" in record or record["via"] is not None
    _, done, hits, _ = read_progress(plots_dir / "g.jsonl.gz")
    assert done
    assert hits == 40


def test_read_progress_survives_a_corrupt_gzip_tail(plots_dir: Path) -> None:
    path = plots_dir / "g.jsonl.gz"
    with gzip.open(path, "wt") as fh:
        for plot in range(1, 5001):
            fh.write(
                json.dumps(
                    {"giscode": "g", "plotno": str(plot), "ok": True, "data": {}}
                )
                + "\n"
            )
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) // 2] + b"\x00garbage")
    highest, done, _, _ = read_progress(path)
    assert not done
    assert 0 < highest < 5000
