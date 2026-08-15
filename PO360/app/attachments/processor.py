"""Attachment persistence and safe document text extraction."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

PO_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def safe_filename(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    return name[:180] or "attachment"


def save_email_message(root: str | Path, account_email: str, message_id: str, content: bytes) -> Path:
    """Save the raw .eml under a folder named for the mailbox's email address."""
    short_mailbox = re.sub(r"[^A-Za-z0-9_.@-]+", "_", account_email)[:80]
    msg_hash = hashlib.sha256(message_id.encode("utf-8", errors="ignore")).hexdigest()[:16]
    directory = Path(root) / short_mailbox / msg_hash
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "email.eml"
    if not path.exists():
        path.write_bytes(content)
    return path


def save_attachment(root: str | Path, account_email: str, message_id: str, filename: str, content: bytes) -> tuple[Path, str]:
    digest = hashlib.sha256(content).hexdigest()
    short_mailbox = re.sub(r"[^A-Za-z0-9_.@-]+", "_", account_email)[:80]
    msg_hash = hashlib.sha256(message_id.encode("utf-8", errors="ignore")).hexdigest()[:16]
    directory = Path(root) / short_mailbox / msg_hash
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{digest[:12]}_{safe_filename(filename)}"
    if not path.exists():
        path.write_bytes(content)
    return path, digest


def save_extracted_json(root: str | Path, account_email: str, message_id: str, payload: dict[str, Any], filename: str = "gemini_extraction.json") -> Path:
    short_mailbox = re.sub(r"[^A-Za-z0-9_.@-]+", "_", account_email)[:80]
    msg_hash = hashlib.sha256(message_id.encode("utf-8", errors="ignore")).hexdigest()[:16]
    directory = Path(root) / short_mailbox / msg_hash
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def extract_pdf_text(path: str | Path) -> str:
    import fitz  # PyMuPDF
    path = Path(path)
    doc = fitz.open(path)
    try:
        text = "\n".join(page.get_text("text") for page in doc)
    finally:
        doc.close()
    return text.strip()


def extract_image_bytes(path: str | Path) -> tuple[bytes, str]:
    path = Path(path)
    return path.read_bytes(), {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")


def looks_like_po_attachment(filename: str, content_type: str = "") -> bool:
    name = filename.lower()
    return Path(name).suffix in PO_EXTENSIONS and bool(re.search(r"\b(po|purchase|order|work.?order|sales.?order)\b", name))
