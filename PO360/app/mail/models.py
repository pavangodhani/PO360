"""Normalized email/attachment models used by the rest of the application."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MailAttachment:
    filename: str
    content_type: str
    content: bytes
    size_bytes: int
    sha256: str = ""


@dataclass
class MailMessage:
    mailbox: str
    message_id: str
    thread_id: str | None
    sender: str
    recipients: str
    subject: str
    received_at: datetime | None
    body_text: str
    body_html: str
    uid: str
    attachments: list[MailAttachment] = field(default_factory=list)
    cc_recipients: str = ""
