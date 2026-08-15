#!/usr/bin/env bash
# Live-tails the current day's log file so you can watch processing in real time.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/python" ]; then
  echo "Virtual environment not found. Run scripts/setup.sh first."
  exit 1
fi

LOG_DIR=$(.venv/bin/python scripts/read_config.py logging.dir logs)
mkdir -p "$LOG_DIR"

echo "Watching log directory: $LOG_DIR"
echo "Press Ctrl+C to stop."
echo

tail -n 50 -f "$LOG_DIR/outlook_analyzer.log"
