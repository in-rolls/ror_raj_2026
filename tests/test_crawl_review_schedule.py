"""Scheduled reviews only advance their deadline after successful execution."""

import json
import runpy
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/schedule_crawl_review.py"
run_review = runpy.run_path(str(SCRIPT))["run_review"]


@pytest.fixture
def state(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Review both crawls.")
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "next_run_epoch": 1000,
                "interval_seconds": 14400,
                "thread_id": "test-thread",
                "prompt_path": str(prompt),
                "codex_path": "/test/codex",
                "odisha_path": "/test/odisha",
            }
        )
    )
    return path


def test_not_due_does_not_run(state):
    with patch("subprocess.run") as run:
        assert not run_review(state, 999)
        run.assert_not_called()


def test_success_advances_four_hours_without_duplicate_send(state):
    with patch(
        "subprocess.run", return_value=subprocess.CompletedProcess([], 0, "queued")
    ) as run:
        assert run_review(state, 1000)
        assert json.loads(state.read_text())["next_run_epoch"] == 15400
        assert not run_review(state, 1001)
        assert run.call_count == 1


def test_failed_review_retains_due_time_for_retry(state):
    with (
        patch("subprocess.run", side_effect=subprocess.CalledProcessError(1, "codex")),
        pytest.raises(subprocess.CalledProcessError),
    ):
        run_review(state, 1000)
    assert json.loads(state.read_text())["next_run_epoch"] == 1000


@pytest.mark.parametrize(("now", "next_due"), [(1060, 15400), (15401, 29800)])
def test_delayed_delivery_preserves_cadence_without_catchup_duplicates(
    state, now, next_due
):
    with patch(
        "subprocess.run", return_value=subprocess.CompletedProcess([], 0, "queued")
    ) as run:
        assert run_review(state, now)
        assert json.loads(state.read_text())["next_run_epoch"] == next_due
        assert not run_review(state, now)
        assert run.call_count == 1
