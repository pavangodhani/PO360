#!/usr/bin/env bash
# Destructive: deletes logs, saved raw emails/attachments and the SQLite database.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/python" ]; then
  echo "Virtual environment not found. Run scripts/setup.sh first."
  exit 1
fi

echo "*** THIS WILL PERMANENTLY DELETE ***"
echo "  - all log files"
echo "  - all saved raw emails/attachments"
echo "  - the SQLite database (all PO/email data)"
echo
read -r -p "Type YES to continue: " CONFIRM
if [ "$CONFIRM" != "YES" ]; then
  echo "Aborted. Nothing was deleted."
  exit 1
fi

LOG_DIR=$(.venv/bin/python scripts/read_config.py logging.dir logs)
RAW_DIR=$(.venv/bin/python scripts/read_config.py processing.raw_email_dir data/raw_emails)
ATTACH_DIR=$(.venv/bin/python scripts/read_config.py processing.attachment_dir data/attachments)
DB_PATH=$(.venv/bin/python scripts/read_config.py database.path data/analyzer.db)

echo "Deleting logs in $LOG_DIR ..."
rm -f "$LOG_DIR"/* 2>/dev/null || true

echo "Deleting raw emails in $RAW_DIR ..."
rm -rf "$RAW_DIR"

echo "Deleting attachments in $ATTACH_DIR ..."
rm -rf "$ATTACH_DIR"

echo "Deleting database $DB_PATH ..."
rm -f "$DB_PATH" "$DB_PATH-wal" "$DB_PATH-shm"

echo "Recreating empty database schema..."
.venv/bin/python -m app.cli --config config/config.json --init

echo
echo "Reset complete."
