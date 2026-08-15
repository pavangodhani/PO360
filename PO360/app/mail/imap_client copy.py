"""IMAP mailbox reader.

The connector intentionally knows nothing about PO rules. It converts provider
mail into a stable internal MailMessage representation.
"""
from __future__ import annotations

import email
import hashlib
import imaplib
import logging
import re
from datetime import datetime
from email import policy
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator

from app.core.config import MailboxConfig
from app.mail.models import MailAttachment, MailMessage

LOGGER = logging.getLogger(__name__)


def decode_mime(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def clean_message_id(value: str | None) -> str:
    value = (value or "").strip()
    return value or "<missing-message-id>"


def parse_received_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None


def body_parts(msg: Message) -> tuple[str, str]:
    plain, html = [], []
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True) or b""
                if isinstance(payload, bytes):
                    payload = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if not isinstance(payload, str):
                payload = str(payload)
            if ctype == "text/plain":
                plain.append(payload)
            elif ctype == "text/html":
                html.append(payload)
    else:
        try:
            payload = msg.get_content()
        except Exception:
            payload = msg.get_payload(decode=True) or b""
            if isinstance(payload, bytes):
                payload = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        if msg.get_content_type() == "text/html":
            html.append(str(payload))
        else:
            plain.append(str(payload))
    html_text = "\n".join(html)
    plain_text = "\n".join(plain)
    if not plain_text.strip() and html_text.strip():
        # Some business mailboxes deliver HTML-only messages. Convert the HTML
        # to readable text so the analyzer sees the same business content.
        try:
            from bs4 import BeautifulSoup
            plain_text = BeautifulSoup(html_text, "html.parser").get_text("\n", strip=True)
        except Exception:
            plain_text = re.sub(r"<[^>]+>", " ", html_text)
    return plain_text, html_text


def attachment_parts(msg: Message) -> list[MailAttachment]:
    results: list[MailAttachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        filename = decode_mime(part.get_filename())
        disposition = (part.get("Content-Disposition") or "").lower()
        if not filename and "attachment" not in disposition:
            continue
        if not filename:
            # Inline unnamed images are not useful for the first PO version.
            continue
        content = part.get_payload(decode=True) or b""
        digest = hashlib.sha256(content).hexdigest()
        results.append(MailAttachment(
            filename=Path(filename).name,
            content_type=part.get_content_type(),
            content=content,
            size_bytes=len(content),
            sha256=digest,
        ))
    return results


class ImapClient:
    def __init__(self, config: MailboxConfig, *, mark_seen: bool = False):
        self.config = config
        self.mark_seen = mark_seen
        self.conn: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None

    def connect(self) -> None:
        LOGGER.info("Connecting to mailbox '%s' (%s)", self.config.name, self.config.imap_host)
        if self.config.use_ssl:
            self.conn = imaplib.IMAP4_SSL(self.config.imap_host, self.config.imap_port, timeout=30)
        else:
            self.conn = imaplib.IMAP4(self.config.imap_host, self.config.imap_port, timeout=30)
        self.conn.login(self.config.email, self.config.password)
        status, _ = self.conn.select(self.config.folder, readonly=not self.mark_seen)
        if status != "OK":
            raise RuntimeError(f"Could not select IMAP folder {self.config.folder!r}: {status}")
        LOGGER.info("Connected to mailbox '%s'", self.config.name)

    def close(self) -> None:
        if not self.conn:
            return
        try:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn.logout()
        except Exception:
            LOGGER.debug("Ignoring IMAP close error", exc_info=True)
        finally:
            self.conn = None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def search_uids(self, start_date: str, include_seen: bool, limit: int) -> list[bytes]:
        if not self.conn:
            raise RuntimeError("IMAP connection is not open")
        # IMAP SINCE is inclusive by date. We intentionally retrieve a little more
        # and let the application/database decide what is new to avoid missing
        # messages around timezone/date boundaries.
        criteria = [f'SINCE "{start_date}"']
        if not include_seen:
            criteria.append("UNSEEN")
        status, data = self.conn.uid("SEARCH", None, *criteria)
        if status != "OK":
            raise RuntimeError(f"IMAP SEARCH failed: {data!r}")
        uids = (data[0] or b"").split()
        return uids[-limit:]

    def fetch_message(self, uid: bytes) -> MailMessage:
        if not self.conn:
            raise RuntimeError("IMAP connection is not open")
        status, data = self.conn.uid("FETCH", uid, "(RFC822)")
        if status != "OK" or not data:
            raise RuntimeError(f"IMAP FETCH failed for UID {uid!r}: {data!r}")
        raw = next((part[1] for part in data if isinstance(part, tuple) and len(part) > 1), None)
        if not raw:
            raise RuntimeError(f"IMAP FETCH returned no RFC822 payload for UID {uid!r}")
        msg = email.message_from_bytes(raw, policy=policy.default)
        plain, html = body_parts(msg)
        message_id = clean_message_id(msg.get("Message-ID"))
        # Message-ID is occasionally missing. UID + mailbox gives us a stable local key.
        if message_id == "<missing-message-id>":
            message_id = f"<uid-{uid.decode(errors='replace')}@local-mailbox>"
        thread_id = msg.get("Thread-Index") or msg.get("References") or msg.get("In-Reply-To")
        return MailMessage(
            mailbox=self.config.name,
            message_id=message_id,
            thread_id=thread_id,
            sender=decode_mime(msg.get("From")),
            recipients=decode_mime(msg.get("To")),
            subject=decode_mime(msg.get("Subject")),
            received_at=parse_received_date(msg.get("Date")),
            body_text=plain,
            body_html=html,
            uid=uid.decode(errors="replace"),
            attachments=attachment_parts(msg),
        )
