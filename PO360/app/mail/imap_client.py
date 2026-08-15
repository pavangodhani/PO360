"""IMAP mailbox reader.

This module is deliberately provider-agnostic: the rest of the application works
with our internal MailMessage/MailAttachment models and does not need to know
whether the mailbox is Gmail, Titan, Microsoft 365, etc.

IMPORTANT:
The IMAP SEARCH syntax must be passed to imaplib as separate search tokens.
For example:

    UID SEARCH SINCE 01-Aug-2026 UNSEEN

NOT as:

    UID SEARCH "SINCE 01-Aug-2026" "UNSEEN"

The previous implementation combined each criterion into one string. Gmail
(and some other IMAP servers) correctly rejected that command with:

    BAD Could not parse command

This replacement fixes that issue.
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

from app.core.config import MailboxConfig
from app.mail.models import MailAttachment, MailMessage

LOGGER = logging.getLogger(__name__)


def decode_mime(value: str | None) -> str:
    """Decode an RFC/MIME encoded email header safely."""
    if not value:
        return ""

    try:
        return str(make_header(decode_header(value)))
    except Exception:
        # A malformed header should not stop processing of the mailbox.
        LOGGER.debug("Could not MIME-decode header; using original value", exc_info=True)
        return value


def clean_message_id(value: str | None) -> str:
    """Return a usable Message-ID, even when the sender omitted one."""
    value = (value or "").strip()
    return value or "<missing-message-id>"


def parse_received_date(value: str | None) -> datetime | None:
    """Parse an RFC 2822 email Date header without failing the whole message."""
    if not value:
        return None

    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        LOGGER.debug("Could not parse email Date header: %r", value, exc_info=True)
        return None


def body_parts(msg: Message) -> tuple[str, str]:
    """Extract readable plain-text and HTML bodies while ignoring attachments."""
    plain: list[str] = []
    html: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue

            disposition = (part.get("Content-Disposition") or "").lower()

            # Attachments are processed separately by attachment_parts().
            if "attachment" in disposition:
                continue

            content_type = part.get_content_type()

            try:
                payload = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True) or b""

                if isinstance(payload, bytes):
                    payload = payload.decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )

            if not isinstance(payload, str):
                payload = str(payload)

            if content_type == "text/plain":
                plain.append(payload)
            elif content_type == "text/html":
                html.append(payload)

    else:
        try:
            payload = msg.get_content()
        except Exception:
            payload = msg.get_payload(decode=True) or b""

            if isinstance(payload, bytes):
                payload = payload.decode(
                    msg.get_content_charset() or "utf-8",
                    errors="replace",
                )

        if msg.get_content_type() == "text/html":
            html.append(str(payload))
        else:
            plain.append(str(payload))

    html_text = "\n".join(html)
    plain_text = "\n".join(plain)

    # Some business mailboxes send HTML-only emails. Convert HTML to readable
    # text so PO information contained in the body is still available.
    if not plain_text.strip() and html_text.strip():
        try:
            from bs4 import BeautifulSoup

            plain_text = BeautifulSoup(
                html_text,
                "html.parser",
            ).get_text("\n", strip=True)
        except Exception:
            # BeautifulSoup is not strictly required for basic operation.
            plain_text = re.sub(r"<[^>]+>", " ", html_text)

    return plain_text, html_text


def thread_root_id(msg: Message, own_message_id: str) -> str:
    """Return a stable thread identifier: the first (root) Message-ID in
    the References chain, so every email in a thread shares the same id.

    Using the raw References header directly does not work because it keeps
    growing with every reply, so no two emails in the same thread would have
    an identical value.
    """
    references = (msg.get("References") or "").split()
    if references:
        return references[0].strip()

    in_reply_to = (msg.get("In-Reply-To") or "").strip()
    if in_reply_to:
        return in_reply_to

    # No reply headers -> this message is the root of its own thread.
    return own_message_id


def attachment_parts(msg: Message) -> list[MailAttachment]:
    """Extract email attachments and calculate a SHA-256 fingerprint."""
    results: list[MailAttachment] = []

    for part in msg.walk():
        if part.is_multipart():
            continue

        filename = decode_mime(part.get_filename())
        disposition = (part.get("Content-Disposition") or "").lower()

        # Include normal attachments and named inline files. Ignore unnamed
        # inline images because they are generally signatures/logos.
        if not filename and "attachment" not in disposition:
            continue

        if not filename:
            continue

        content = part.get_payload(decode=True) or b""
        digest = hashlib.sha256(content).hexdigest()

        results.append(
            MailAttachment(
                filename=Path(filename).name,
                content_type=part.get_content_type(),
                content=content,
                size_bytes=len(content),
                sha256=digest,
            )
        )

    return results


class ImapClient:
    """Small, defensive IMAP client used by the processing pipeline."""

    def __init__(self, config: MailboxConfig, *, mark_seen: bool = False):
        self.config = config
        self.mark_seen = mark_seen
        self.conn: imaplib.IMAP4 | imaplib.IMAP4_SSL | None = None

    def connect(self) -> None:
        """Open, authenticate, and select the configured mailbox folder."""
        LOGGER.info(
            "Connecting to mailbox '%s' (%s)",
            self.config.name,
            self.config.imap_host,
        )

        if self.config.use_ssl:
            self.conn = imaplib.IMAP4_SSL(
                self.config.imap_host,
                self.config.imap_port,
                timeout=30,
            )
        else:
            self.conn = imaplib.IMAP4(
                self.config.imap_host,
                self.config.imap_port,
                timeout=30,
            )

        try:
            self.conn.login(self.config.email, self.config.password)

            status, _ = self.conn.select(
                self.config.folder,
                readonly=not self.mark_seen,
            )

            if status != "OK":
                raise RuntimeError(
                    f"Could not select IMAP folder {self.config.folder!r}: {status}"
                )

            LOGGER.info("Connected to mailbox '%s'", self.config.name)

        except Exception:
            # Ensure a failed connection does not leave a half-open socket.
            self.close()
            raise

    def close(self) -> None:
        """Close the selected mailbox and logout safely."""
        if not self.conn:
            return

        try:
            try:
                self.conn.close()
            except Exception:
                # CLOSE can fail when SELECT never succeeded. Logout is still
                # attempted below.
                LOGGER.debug("IMAP CLOSE failed during shutdown", exc_info=True)

            try:
                self.conn.logout()
            except Exception:
                LOGGER.debug("IMAP LOGOUT failed during shutdown", exc_info=True)

        finally:
            self.conn = None

    def __enter__(self) -> "ImapClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @staticmethod
    def _build_search_criteria(
        start_date: str,
        include_seen: bool,
    ) -> list[str]:
        """Build IMAP SEARCH tokens.

        IMAP SEARCH is token based. Keeping each token separate is important.
        The server ultimately receives something equivalent to:

            UID SEARCH SINCE 01-Aug-2026 UNSEEN

        The date is normalized to the IMAP DD-Mon-YYYY representation when
        possible, which is accepted consistently by IMAP servers.
        """
        try:
            parsed = datetime.strptime(start_date, "%Y-%m-%d")
            imap_date = parsed.strftime("%d-%b-%Y")
        except ValueError:
            # Allow an already valid IMAP-style date to pass through, while
            # still producing a clear error for obviously invalid input later.
            if not re.fullmatch(r"\d{1,2}-[A-Za-z]{3}-\d{4}", start_date):
                raise ValueError(
                    "start_date must be YYYY-MM-DD or DD-Mon-YYYY; "
                    f"received {start_date!r}"
                )
            imap_date = start_date

        criteria = ["SINCE", imap_date]

        if not include_seen:
            criteria.append("UNSEEN")

        return criteria

    def search_uids(
        self,
        start_date: str,
        include_seen: bool,
        limit: int,
    ) -> list[bytes]:
        """Find message UIDs matching the configured date/read-status filter.

        This is the corrected implementation for the Gmail BAD
        "Could not parse command" error.

        We deliberately use UID SEARCH because the returned identifiers are
        actual message UIDs. Those UIDs can safely be passed to UID FETCH later.
        """
        if not self.conn:
            raise RuntimeError("IMAP connection is not open")

        if limit <= 0:
            return []

        criteria = self._build_search_criteria(start_date, include_seen)

        LOGGER.debug(
            "Running IMAP UID SEARCH for mailbox '%s': %s",
            self.config.name,
            criteria,
        )

        try:
            # IMPORTANT:
            # Pass SEARCH criteria as separate arguments.
            #
            # Correct:
            #   uid("SEARCH", None, "SINCE", "01-Aug-2026", "UNSEEN")
            #
            # Incorrect (the old bug):
            #   uid("SEARCH", None, 'SINCE "01-Aug-2026"', "UNSEEN")
            status, data = self.conn.uid("SEARCH", None, *criteria)

        except imaplib.IMAP4.error:
            LOGGER.error(
                "IMAP UID SEARCH failed for mailbox '%s'. Criteria=%s",
                self.config.name,
                criteria,
                exc_info=True,
            )
            raise

        if status != "OK":
            raise RuntimeError(f"IMAP UID SEARCH failed: {data!r}")

        raw_uids = data[0] if data else b""
        if isinstance(raw_uids, str):
            raw_uids = raw_uids.encode()

        uids = raw_uids.split()

        # UID SEARCH normally returns ascending UIDs. We keep the newest
        # `limit` messages while preserving server order.
        return uids[-limit:]

    def fetch_raw_message(self, uid: bytes) -> bytes:
        """Fetch raw RFC822 bytes for a single UID."""
        if not self.conn:
            raise RuntimeError("IMAP connection is not open")

        if not uid:
            raise ValueError("Cannot fetch an empty IMAP UID")

        status, data = self.conn.uid("FETCH", uid, "(RFC822)")

        if status != "OK" or not data:
            raise RuntimeError(f"IMAP FETCH failed for UID {uid!r}: {data!r}")

        raw = next(
            (
                part[1]
                for part in data
                if isinstance(part, tuple) and len(part) > 1
            ),
            None,
        )

        if not raw:
            raise RuntimeError(f"IMAP FETCH returned no RFC822 payload for UID {uid!r}")

        return raw

    def fetch_message(self, uid: bytes) -> MailMessage:
        """Fetch and normalize one email by UID."""
        raw = self.fetch_raw_message(uid)
        msg = email.message_from_bytes(raw, policy=policy.default)

        plain, html = body_parts(msg)

        message_id = clean_message_id(msg.get("Message-ID"))

        if message_id == "<missing-message-id>":
            message_id = f"<uid-{uid.decode(errors='replace')}@local-mailbox>"

        thread_id = thread_root_id(msg, message_id)

        return MailMessage(
            mailbox=self.config.name,
            message_id=message_id,
            thread_id=thread_id,
            sender=decode_mime(msg.get("From")),
            recipients=decode_mime(msg.get("To")),
            cc_recipients=decode_mime(msg.get("Cc")),
            subject=decode_mime(msg.get("Subject")),
            received_at=parse_received_date(msg.get("Date")),
            body_text=plain,
            body_html=html,
            uid=uid.decode(errors="replace"),
            attachments=attachment_parts(msg),
        )

    def search_header(self, header_name: str, value: str) -> list[bytes]:
        """Find UIDs of messages whose given header contains `value` (substring).

        Used to reconstruct a full email thread via References/Message-ID,
        regardless of the configured start date, since IMAP SEARCH HEADER does
        not filter by date.
        """
        if not self.conn:
            raise RuntimeError("IMAP connection is not open")
        if not value:
            return []
        try:
            status, data = self.conn.uid("SEARCH", None, "HEADER", header_name, f'"{value}"')
        except imaplib.IMAP4.error:
            LOGGER.error("IMAP HEADER search failed for %s=%s", header_name, value, exc_info=True)
            return []
        if status != "OK":
            return []
        raw_uids = data[0] if data else b""
        if isinstance(raw_uids, str):
            raw_uids = raw_uids.encode()
        return raw_uids.split()

    def find_thread_uids(self, root_message_id: str) -> list[bytes]:
        """Find all UIDs belonging to the same thread as `root_message_id`.

        Combines the root message itself with every message that references it
        (direct replies and later replies-to-replies), independent of date.
        """
        root_message_id = (root_message_id or "").strip()
        if not root_message_id:
            return []
        uids: set[bytes] = set()
        uids.update(self.search_header("Message-ID", root_message_id))
        uids.update(self.search_header("References", root_message_id))
        uids.update(self.search_header("In-Reply-To", root_message_id))
        return sorted(uids, key=lambda u: int(u) if u.isdigit() else 0)

    def search_since_datetime(self, since: datetime, include_seen: bool, limit: int) -> list[bytes]:
        """Find UIDs on/after a given date (IMAP SINCE is date-granularity only).

        Callers must further filter results by the exact parsed Date header to
        honor time-of-day precision, since IMAP SINCE cannot do that itself.
        """
        imap_date = since.strftime("%d-%b-%Y")
        return self._search_uids_by_imap_date(imap_date, include_seen, limit)

    def _search_uids_by_imap_date(self, imap_date: str, include_seen: bool, limit: int) -> list[bytes]:
        if not self.conn:
            raise RuntimeError("IMAP connection is not open")
        if limit <= 0:
            return []
        criteria = ["SINCE", imap_date]
        if not include_seen:
            criteria.append("UNSEEN")
        status, data = self.conn.uid("SEARCH", None, *criteria)
        if status != "OK":
            raise RuntimeError(f"IMAP UID SEARCH failed: {data!r}")
        raw_uids = data[0] if data else b""
        if isinstance(raw_uids, str):
            raw_uids = raw_uids.encode()
        uids = raw_uids.split()
        return uids[-limit:] if limit else uids