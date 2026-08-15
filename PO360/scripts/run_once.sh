#!/usr/bin/env bash
# Runs one mailbox processing cycle and exits.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/python" ]; then
  echo "Virtual environment not found. Run scripts/setup.sh first."
  exit 1
fi

.venv/bin/python -m app.cli --config config/config.json --once
exit_code=$?
echo "Application exit code: $exit_code"
exit $exit_code
