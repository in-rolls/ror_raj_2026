#!/bin/zsh
# Full-state crawl, resumable: run under nohup and leave it. Nagaur (21) goes
# first so the pilot villages complete; then every district in code order.
# caffeinate keeps the machine and its network awake for the crawl's lifetime.
set -u
cd "$(dirname "$0")"
LOG=logs/crawl.log
echo "$(date -Iseconds) start" >> "$LOG"
caffeinate -dims uv run rajasthan-ror-list --workers 4 >> "$LOG" 2>&1
caffeinate -dims uv run rajasthan-ror-fetch --districts 21 --workers 8 --pause 0.1 >> "$LOG" 2>&1
caffeinate -dims uv run rajasthan-ror-fetch --workers 8 --pause 0.1 >> "$LOG" 2>&1
echo "$(date -Iseconds) end" >> "$LOG"
