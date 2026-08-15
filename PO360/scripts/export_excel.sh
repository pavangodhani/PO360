#!/usr/bin/env bash
# Exports PO Details / PO Distribution / PO Logs / Email Details to Excel,
# using export.from_datetime/export.to_datetime from config/config.json.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/python" ]; then
  echo "Virtual environment not found. Run scripts/setup.sh first."
  exit 1
fi

.venv/bin/python -m app.cli --config config/config.json --export
exit_code=$?
if [ $exit_code -eq 0 ]; then
  echo "Export complete. See output/ (output.export_dir in config.json) for the .xlsx file."
else
  echo "Export FAILED - see logs/errors.log for details."
fi
exit $exit_code
