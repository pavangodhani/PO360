"""Centralized file + console logging.

A new log file is created per calendar day (TimedRotatingFileHandler), named
outlook_analyzer_YYYY-MM-DD.log, so it is easy to find exactly what happened
on a given day/run without wading through one giant log file. Errors and
warnings are additionally duplicated into a separate errors_YYYY-MM-DD.log
file so failures can be found quickly without reproducing the issue.
"""
from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_dir: str = "logs", level: str = "INFO") -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers when tests or repeated CLI invocations initialize logging.
    if logger.handlers:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and not isinstance(handler, TimedRotatingFileHandler):
                handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        return logger

    formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

    file_handler = TimedRotatingFileHandler(
        Path(log_dir) / "outlook_analyzer.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    file_handler.suffix = "%Y-%m-%d.log"
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    error_handler = TimedRotatingFileHandler(
        Path(log_dir) / "errors.log",
        when="midnight",
        backupCount=30,
        encoding="utf-8",
        utc=False,
    )
    error_handler.suffix = "%Y-%m-%d.log"
    error_handler.setFormatter(formatter)
    error_handler.setLevel(logging.WARNING)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console.setLevel(getattr(logging, level.upper(), logging.INFO))

    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    logger.addHandler(console)
    return logger
