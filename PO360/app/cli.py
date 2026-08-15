"""Command-line entry point.

Examples:
    python -m app.cli --config config/config.json --once
    python -m app.cli --config config/config.json --init
    python -m app.cli --config config/config.json --check-config
"""
from __future__ import annotations

import argparse
import logging

from app.core.config import load_config, parse_start_datetime
from app.core.logging_setup import setup_logging
from app.database.db import Database
from app.processing.runner import ApplicationRunner

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Outlook Email Analyzer - PO processing application")
    p.add_argument("--config", default="config/config.json", help="Path to config JSON")
    p.add_argument("--once", action="store_true", help="Run one mailbox processing cycle and exit")
    p.add_argument(
        "--reprocess-from",
        help="Re-extract all emails strictly after this date/time without deleting data or changing the normal resume checkpoint",
    )
    p.add_argument("--init", action="store_true", help="Create the SQLite database file and schema (idempotent)")
    p.add_argument("--check-config", action="store_true", help="Validate config.json, mailbox logins, and the Gemini API key without processing any email")
    p.add_argument("--export", action="store_true", help="Export PO Details/PO Distribution/PO Logs/Email Details to Excel using export.from_datetime/to_datetime in config.json")
    return p


def _check_config(config) -> int:
    """Validate config values, mailbox connectivity, and the Gemini API key."""
    ok = True

    if config.ai.enabled and not config.ai.api_key:
        LOGGER.error("AI is enabled but no Gemini api_key is configured.")
        ok = False
    elif config.ai.enabled:
        try:
            from app.ai.gemini import GeminiClient
            GeminiClient(config.ai)._discover_model()
            LOGGER.info("Gemini API key is valid and a usable model was found.")
        except Exception as exc:
            LOGGER.error("Gemini API key/model check failed: %s", exc)
            ok = False

    if not config.mailboxes:
        LOGGER.error("No mailboxes are configured.")
        ok = False

    for mailbox in config.mailboxes:
        if not mailbox.enabled:
            LOGGER.info("Mailbox '%s' (%s) is disabled; skipping connectivity check.", mailbox.name, mailbox.email)
            continue
        from app.mail.imap_client import ImapClient
        try:
            with ImapClient(mailbox, mark_seen=False):
                LOGGER.info("Mailbox '%s' (%s): IMAP login/select OK.", mailbox.name, mailbox.email)
        except Exception as exc:
            LOGGER.error("Mailbox '%s' (%s): IMAP connectivity check failed: %s", mailbox.name, mailbox.email, exc)
            ok = False

    return 0 if ok else 1


def _export(config, db) -> int:
    """Export PO Details/PO Distribution/PO Logs/Email Details to Excel for the
    from_datetime/to_datetime window configured in config.json (export section)."""
    if not config.export.from_datetime or not config.export.to_datetime:
        LOGGER.error(
            "export.from_datetime and export.to_datetime must be set in config.json before running --export."
        )
        return 1

    from_dt = parse_start_datetime(config.export.from_datetime)
    to_dt = parse_start_datetime(config.export.to_datetime)
    if from_dt > to_dt:
        LOGGER.error(
            "export.from_datetime (%s) is after export.to_datetime (%s) in config.json.",
            config.export.from_datetime, config.export.to_datetime,
        )
        return 1

    try:
        from app.reporting.data_export import export_data
        output_path = export_data(db, from_dt, to_dt, config.output.export_dir)
        LOGGER.info("Export complete: %s", output_path)
        return 0
    except Exception as exc:
        LOGGER.error("Excel export failed: %s", exc, exc_info=True)
        return 1


def main() -> int:
    args = build_parser().parse_args()
    setup_logging("logs", "INFO")
    try:
        config = load_config(args.config)
        setup_logging(config.logging.dir, config.logging.level)
        db = Database(config.database.path)
        if args.init:
            LOGGER.info("Database schema initialized at %s", config.database.path)
            return 0
        if args.check_config:
            return _check_config(config)
        if args.export:
            return _export(config, db)
        if args.once or args.reprocess_from:
            reprocess_from = parse_start_datetime(args.reprocess_from) if args.reprocess_from else None
            return ApplicationRunner(config, db).run_once(reprocess_from=reprocess_from)
        LOGGER.error("No action selected. Use --once, --reprocess-from, --init, --check-config or --export.")
        return 2
    except Exception as exc:
        LOGGER.critical("Application startup failure: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
