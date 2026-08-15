#!/usr/bin/env bash
# Validates config.json values, mailbox IMAP logins, and the Gemini API key
# without processing any email.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/python" ]; then
  echo "Virtual environment not found. Run scripts/setup.sh first."
  exit 1
fi

.venv/bin/python -m app.cli --config config/config.json --check-config
exit_code=$?
if [ $exit_code -eq 0 ]; then
  echo "Config check passed."
else
  echo "Config check FAILED - see logs/errors.log for details."
fi
exit $exit_code
