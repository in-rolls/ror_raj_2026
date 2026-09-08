#!/bin/zsh
# Full-state crawl, resumable: run under nohup and leave it. Nagaur (21) goes
# first so the pilot villages complete; then every district in code order.
set -u
cd "$(dirname "$0")"
LOG=logs/crawl.log
echo "$(date -Iseconds) start" >> "$LOG"
uv run rajasthan-ror-list --workers 4 >> "$LOG" 2>&1
uv run rajasthan-ror-fetch --districts 21 --workers 6 --miss-run 60 --pause 0.1 >> "$LOG" 2>&1
uv run rajasthan-ror-fetch --workers 6 --miss-run 60 --pause 0.1 >> "$LOG" 2>&1
echo "$(date -Iseconds) end" >> "$LOG"
