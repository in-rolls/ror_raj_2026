#!/bin/zsh
# Supervisor restarts unsuccessful passes; completed sheets are skipped on resume.
set -eu
cd "$(dirname "$0")"
mkdir -p logs
LOG=logs/crawl.log
if [[ -L raw && ! -d raw ]]; then
    printf '%s data volume unavailable; retrying after five minutes\n' "$(date -Iseconds)" >> "$LOG"
    sleep 300
    exit 1
fi
echo "$(date -Iseconds) start" >> "$LOG"
if [[ ! -f raw/villages.parquet ]]; then
    /usr/bin/caffeinate -dims .venv/bin/python -m rajasthan_ror.locations --workers 4 >> "$LOG" 2>&1
fi
priority_args=()
if [[ -f raw/repair-sheets.txt ]]; then
    priority_args=(--priority-file raw/repair-sheets.txt)
fi
if /usr/bin/caffeinate -dims .venv/bin/python -m rajasthan_ror.plots --workers 8 --pause 0.1 "${priority_args[@]}" >> "$LOG" 2>&1; then
    exit 0
fi
echo "$(date -Iseconds) incomplete pass; retrying after five minutes" >> "$LOG"
sleep 300
exit 1
