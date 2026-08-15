#!/usr/bin/env bash
# Re-creates any missing tables/indexes without deleting existing data
# (all CREATE statements use IF NOT EXISTS).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/python" ]; then
  echo "Virtual environment not found. Run scripts/setup.sh first."
  exit 1
fi

.venv/bin/python -m app.cli --config config/config.json --init
echo "Schema check/creation complete. No existing data was deleted."
