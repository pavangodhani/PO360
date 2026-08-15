#!/usr/bin/env bash
# Installs dependencies and creates the SQLite database + schema.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "=== Outlook Email Analyzer setup ==="

if ! command -v python3.12 >/dev/null 2>&1; then
  echo "Python 3.12 was not found on PATH. Install Python 3.12.x and re-run this script."
  exit 1
fi

PYTHON_BIN="python3.12"

if [ -x ".venv/bin/python" ] && [ "$(.venv/bin/python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" != "3.12" ]; then
  echo "Existing .venv uses $(.venv/bin/python --version), but this project requires Python 3.12.x."
  echo "Remove it with: rm -rf .venv"
  echo "Then re-run this script after installing Python 3.12.x."
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment..."
  "$PYTHON_BIN" -m venv .venv
fi

echo "Installing dependencies..."
.venv/bin/python -m pip install --upgrade pip >/dev/null
.venv/bin/python -m pip install -r requirements.txt

if [ ! -f "config/config.json" ]; then
  echo "Creating config/config.json from config.example.json - EDIT IT before running."
  cp "config/config.example.json" "config/config.json"
fi

echo "Creating SQLite database and schema..."
.venv/bin/python -m app.cli --config config/config.json --init

echo
echo "Setup complete. Edit config/config.json with real mailbox/API credentials,"
echo "then run scripts/check_config.sh to validate them, and scripts/run_once.sh to process email."
