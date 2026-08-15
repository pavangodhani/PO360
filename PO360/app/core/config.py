"""Configuration loading and validation.

The application deliberately keeps operational settings outside the code so that
mailboxes, start date/time, save-to-disk behaviour, database/log paths and AI
settings can be changed without touching the implementation. Everything the
application needs at runtime should be reachable from a single config.json file.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

# Accepted human-friendly date/time formats for `start_datetime` values, e.g.
# "10 Aug 2026 14:05:00" as used throughout the requirements.
_DATETIME_FORMATS = (
    "%d %b %Y %H:%M:%S",
    "%d %b %Y %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
)


def parse_start_datetime(value: str) -> datetime:
    """Parse a configured start date/time using any of the accepted formats."""
    value = (value or "").strip()
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(
        f"start_datetime {value!r} is not in a recognized format. "
        "Use e.g. '10 Aug 2026 14:05:00' or '2026-08-10 14:05:00'."
    )


class MailboxConfig(BaseModel):
    name: str
    email: str
    password: str
    imap_host: str
    imap_port: int = 993
    use_ssl: bool = True
    folder: str = "INBOX"
    enabled: bool = True
    # First-run starting point for this mailbox. Subsequent runs resume from
    # the last successfully processed email's date-time (see mailbox_state table).
    start_datetime: str = "2026-01-01 00:00:00"

    @field_validator("start_datetime")
    @classmethod
    def validate_start_datetime(cls, value: str) -> str:
        parse_start_datetime(value)
        return value


class AIConfig(BaseModel):
    enabled: bool = True
    provider: str = "gemini_rest"
    api_key: str = ""
    model: str = "gemini-3.5-flash-lite"
    timeout_seconds: int = 90
    max_retries: int = 3
    retry_backoff_seconds: float = 2.0
    max_pdf_bytes: int = 15 * 1024 * 1024


class ProcessingConfig(BaseModel):
    max_emails_per_run: int = Field(default=200, ge=1)
    mark_seen: bool = False
    include_seen_messages: bool = True
    # How many prior emails (in the same thread) to include as AI context.
    thread_context_limit: int = Field(default=25, ge=1)
    # Save raw .eml / attachments to disk for debugging. Defaults to True per spec.
    save_raw_emails: bool = True
    raw_email_dir: str = "data/raw_emails"
    attachment_dir: str = "data/attachments"
    retain_attachments: bool = True


class OutputConfig(BaseModel):
    excel_path: str = "output/PO_Report.xlsx"
    # Directory the date-range Excel export (--export) writes into.
    export_dir: str = "output"


class ExportConfig(BaseModel):
    """From/To window for the raw-table Excel export (--export).

    Kept in config.json (rather than as CLI args) for now, per the testing
    workflow requested: edit these two values and re-run scripts/export_excel.
    """
    from_datetime: str = ""
    to_datetime: str = ""

    @field_validator("from_datetime", "to_datetime")
    @classmethod
    def validate_datetime(cls, value: str) -> str:
        if value:
            parse_start_datetime(value)
        return value


class DatabaseConfig(BaseModel):
    path: str = "data/analyzer.db"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    dir: str = "logs"

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        value = value.upper()
        if value not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("logging.level must be DEBUG, INFO, WARNING, ERROR or CRITICAL")
        return value


class AppConfig(BaseModel):
    app_name: str = "Outlook Email Analyzer"
    env: str = "prod"
    timezone: str = "Asia/Kolkata"
    processing: ProcessingConfig = ProcessingConfig()
    output: OutputConfig = OutputConfig()
    database: DatabaseConfig = DatabaseConfig()
    logging: LoggingConfig = LoggingConfig()
    ai: AIConfig = AIConfig()
    export: ExportConfig = ExportConfig()
    mailboxes: list[MailboxConfig] = Field(default_factory=list)

    @field_validator("env")
    @classmethod
    def validate_env(cls, value: str) -> str:
        normalized = (value or "prod").strip().lower()
        aliases = {"development": "dev", "dev": "dev", "production": "prod", "prod": "prod", "qa": "qa"}
        if normalized not in aliases:
            # A typo here (e.g. "devl") must not crash the entire application at
            # startup - fall back to "prod" and rely on logging to surface it.
            import logging
            logging.getLogger(__name__).warning(
                "Unrecognized 'env' value %r in config.json; expected dev, prod, or qa. Falling back to 'prod'.",
                value,
            )
            return "prod"
        return aliases[normalized]

    # Backward-compatible convenience accessor used by a few call sites/tests.
    @property
    def logging_level(self) -> str:
        return self.logging.level


def load_config(path: str | Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {path}. Copy config/config.example.json to config/config.json and edit it."
        )
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return AppConfig.model_validate(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    except ValidationError as exc:
        raise ValueError(f"Invalid configuration in {path}:\n{exc}") from exc
