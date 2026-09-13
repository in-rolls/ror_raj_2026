"""Queue the authorized crawl review in its existing Codex chat every four hours."""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


def run_review(state_path: Path, now: float) -> bool:
    state = json.loads(state_path.read_text())
    if now < state["next_run_epoch"]:
        return False
    prompt = Path(state["prompt_path"]).read_text()
    result = subprocess.run(  # noqa: S603
        [
            state["codex_path"],
            "queue",
            "--thread",
            state["thread_id"],
            "--message",
            prompt,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    state["last_queued_at"] = datetime.fromtimestamp(now, UTC).isoformat()
    due = state["next_run_epoch"]
    interval = state["interval_seconds"]
    elapsed_slots = int((now - due) // interval) + 1
    state["next_run_epoch"] = due + elapsed_slots * interval
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(state_path)
    print(result.stdout.strip(), flush=True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=Path("logs/review-schedule.json"))
    args = parser.parse_args()
    with args.state.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                run_review(args.state, time.time())
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                print(
                    f"Review could not run; retrying in one minute: {error}", flush=True
                )
            time.sleep(60)


if __name__ == "__main__":
    main()
