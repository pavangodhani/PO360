"""Read emails and full threads, call Gemini, and persist results to the database.

Per mailbox (email address in config), each run:
  1. Resumes from the last successfully processed email's date-time (or the
     configured `start_datetime` on the very first run).
  2. Reads every new email one at a time, oldest first, including its full
     thread history (even messages older than `start_datetime`) so the AI has
     complete context.
  3. Optionally saves the raw .eml and attachments to disk, in a folder named
     after the mailbox's email address.
  4. Sends the email + thread context + attachments to Gemini.
  5. Persists the AI's classification/extraction into the database.

Excel export is intentionally NOT invoked here - see reporting/excel.py, which
is deferred to a future task.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.ai.gemini import GeminiClient, GeminiError
from app.analyzer.po.engine import POEngine
from app.attachments.processor import save_attachment, save_email_message
from app.core.config import AppConfig, MailboxConfig, parse_start_datetime
from app.database.db import Database
from app.mail.imap_client import ImapClient
from app.mail.models import MailMessage

LOGGER = logging.getLogger(__name__)

_CONNECT_RETRIES = 3
_CONNECT_BACKOFF_SECONDS = 3.0


class ApplicationRunner:
    def __init__(self, config: AppConfig, db: Database | None = None):
        self.config = config
        self.db = db or Database(config.database.path)
        self.ai = GeminiClient(config.ai)
        self.engine = POEngine(self.db)
        # Per-run cache of thread context so repeated messages in the same
        # thread do not re-fetch the whole thread from IMAP every time.
        self._thread_cache: dict[str, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    # Mailbox connection with retry
    # ------------------------------------------------------------------
    def _connect_with_retry(self, mailbox: MailboxConfig) -> ImapClient:
        last_exc: Exception | None = None
        for attempt in range(1, _CONNECT_RETRIES + 1):
            client = ImapClient(mailbox, mark_seen=self.config.processing.mark_seen)
            try:
                client.connect()
                return client
            except Exception as exc:
                last_exc = exc
                LOGGER.warning(
                    "Mailbox '%s' (%s): connection attempt %s/%s failed: %s",
                    mailbox.name, mailbox.email, attempt, _CONNECT_RETRIES, exc,
                )
                if attempt < _CONNECT_RETRIES:
                    time.sleep(_CONNECT_BACKOFF_SECONDS * attempt)
        assert last_exc is not None
        raise last_exc

    # ------------------------------------------------------------------
    # Thread context assembly
    # ------------------------------------------------------------------
    def _thread_context_entries(self, client: ImapClient, mailbox: MailboxConfig, message: MailMessage) -> list[dict[str, Any]]:
        """Return prior-thread entries (oldest first), combining DB history with
        any live IMAP fetch needed to fill in messages older than start_datetime."""
        thread_id = message.thread_id
        if not thread_id:
            return []

        cached = self._thread_cache.get(thread_id)
        if cached is None:
            entries: list[dict[str, Any]] = []
            seen_message_ids: set[str] = set()

            for row in self.db.get_thread_emails(mailbox.email, thread_id, self.config.processing.thread_context_limit):
                entries.append({
                    "message_id": row["message_id"], "subject": row["subject"], "sender": row["sender_email"],
                    "received_at": row["received_at"], "body_text": row["body_text"],
                })
                seen_message_ids.add(row["message_id"])

            try:
                thread_uids = client.find_thread_uids(thread_id)
            except Exception:
                LOGGER.warning("Thread expansion failed for thread '%s'; using DB history only", thread_id, exc_info=True)
                thread_uids = []

            for uid in thread_uids:
                try:
                    fetched = client.fetch_message(uid)
                except Exception:
                    LOGGER.warning("Could not fetch thread message UID %r for thread '%s'", uid, thread_id, exc_info=True)
                    continue
                if fetched.message_id in seen_message_ids or fetched.message_id == message.message_id:
                    continue
                seen_message_ids.add(fetched.message_id)
                entries.append({
                    "message_id": fetched.message_id, "subject": fetched.subject, "sender": fetched.sender,
                    "received_at": fetched.received_at.isoformat() if fetched.received_at else "",
                    "body_text": fetched.body_text,
                })

            entries.sort(key=lambda e: e["received_at"] or "")
            cached = entries
            self._thread_cache[thread_id] = cached

        return [e for e in cached if e["message_id"] != message.message_id]

    @staticmethod
    def _render_thread_context(entries: list[dict[str, Any]]) -> str:
        pieces = []
        for e in entries:
            if not (e.get("subject") or e.get("body_text")):
                continue
            pieces.append(
                f"--- EMAIL ---\nDATE: {e.get('received_at') or ''}\nFROM: {e.get('sender') or ''}\n"
                f"SUBJECT: {e.get('subject') or ''}\nBODY: {(e.get('body_text') or '')[:6000]}"
            )
        return "\n".join(pieces)

    def _remember_in_cache(self, message: MailMessage) -> None:
        if not message.thread_id:
            return
        entries = self._thread_cache.setdefault(message.thread_id, [])
        entries.append({
            "message_id": message.message_id, "subject": message.subject, "sender": message.sender,
            "received_at": message.received_at.isoformat() if message.received_at else "",
            "body_text": message.body_text,
        })
        entries.sort(key=lambda e: e["received_at"] or "")

    @staticmethod
    def _merge_ai_results(results: list[dict[str, Any]]) -> dict[str, Any]:
        """Combine one Gemini result per attachment into one email result."""
        if not results:
            raise GeminiError("No usable Gemini extraction result was returned")

        strongest = max(results, key=lambda result: int(result.get("po_confidence") or 0))
        merged = {
            "is_po_related": any(bool(result.get("is_po_related")) for result in results),
            "po_remarks": strongest.get("po_remarks") or "",
            "po_confidence": strongest.get("po_confidence") or 1,
            "email_conclusion": strongest.get("email_conclusion") or "",
            "pos": [],
            "has_unresolved_distribution": False,
            "unresolved_distribution": {},
        }
        pos_by_number: dict[str, dict[str, Any]] = {}

        for result in results:
            if result.get("has_unresolved_distribution"):
                merged["has_unresolved_distribution"] = True
                if not merged["unresolved_distribution"]:
                    merged["unresolved_distribution"] = result.get("unresolved_distribution") or {}

            for extracted_po in result.get("pos") or []:
                po_number = (extracted_po.get("po_number") or "").strip().upper()
                if not po_number:
                    continue

                current = pos_by_number.setdefault(po_number, {
                    "po_number": extracted_po.get("po_number") or "",
                    "is_existing_po": bool(extracted_po.get("is_existing_po")),
                    "company_name": "",
                    "from_company": "",
                    "to_company": "",
                    "sent_by": "",
                    "po_datetime": "",
                    "total_goods": "",
                    "distributions": [],
                })
                for field in ("company_name", "from_company", "to_company", "sent_by", "po_datetime", "total_goods"):
                    if extracted_po.get(field):
                        current[field] = extracted_po[field]
                current["is_existing_po"] = current["is_existing_po"] or bool(extracted_po.get("is_existing_po"))

                distribution_by_address = {
                    (distribution.get("address") or "").strip().upper(): distribution
                    for distribution in current["distributions"]
                }
                for extracted_distribution in extracted_po.get("distributions") or []:
                    address_key = (extracted_distribution.get("address") or "").strip().upper()
                    if not address_key:
                        continue
                    distribution = distribution_by_address.get(address_key)
                    if distribution is None:
                        distribution = dict(extracted_distribution)
                        current["distributions"].append(distribution)
                        distribution_by_address[address_key] = distribution
                    else:
                        for field, value in extracted_distribution.items():
                            if value not in (None, "", False):
                                distribution[field] = value

        merged["pos"] = list(pos_by_number.values())
        return merged

    # ------------------------------------------------------------------
    # Per-email processing
    # ------------------------------------------------------------------
    def _process_one_email(self, client: ImapClient, mailbox: MailboxConfig, message: MailMessage, raw_bytes: bytes) -> dict[str, Any]:
        LOGGER.info(
            "Mailbox '%s': processing email message_id=%s subject=%r received_at=%s attachments=%s",
            mailbox.email, message.message_id, message.subject, message.received_at, len(message.attachments),
        )

        if self.config.processing.save_raw_emails:
            try:
                save_email_message(self.config.processing.raw_email_dir, mailbox.email, message.message_id, raw_bytes)
                for attachment in message.attachments:
                    save_attachment(
                        self.config.processing.raw_email_dir, mailbox.email, message.message_id,
                        attachment.filename, attachment.content,
                    )
                LOGGER.debug("Saved raw email + %s attachment(s) to disk for %s", len(message.attachments), message.message_id)
            except Exception:
                # Disk-save failures must never prevent AI extraction/DB persistence.
                LOGGER.error("Failed saving raw email/attachments to disk for %s", message.message_id, exc_info=True)

        email_id = self.db.upsert_email_details({
            "primary_email": mailbox.email,
            "message_id": message.message_id,
            "thread_id": message.thread_id,
            "subject": message.subject,
            "sender_email": message.sender,
            "to_recipients": message.recipients,
            "cc_recipients": message.cc_recipients,
            "received_at": message.received_at.isoformat() if message.received_at else None,
            "body_text": message.body_text,
            "body_html": message.body_html,
            "attachment_count": len(message.attachments),
            "attachment_names": ", ".join(a.filename for a in message.attachments),
        })

        thread_entries = self._thread_context_entries(client, mailbox, message)
        thread_context = self._render_thread_context(thread_entries)
        known_po_numbers = self.engine.known_po_numbers(message.thread_id)

        supported_attachments = [
            attachment for attachment in message.attachments
            if attachment.content_type == "application/pdf"
            or attachment.filename.lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp"))
        ]

        try:
            ai_results: list[dict[str, Any]] = []
            for attachment in supported_attachments:
                if attachment.size_bytes > self.config.ai.max_pdf_bytes:
                    LOGGER.warning("Skipping oversized attachment for AI: %s", attachment.filename)
                    continue
                try:
                    LOGGER.info("Calling Gemini for attachment '%s' in message '%s'", attachment.filename, message.message_id)
                    ai_results.append(self.ai.extract(
                        subject=message.subject,
                        body=(
                            "ATTACHMENT-ONLY EXTRACTION: Analyze only the attached document. "
                            "Extract this document's PO number and every goods/quantity line from its PO table. "
                            "Do not infer or assign addresses, branches, contacts, or PO numbers from any other document."
                        ),
                        sender=message.sender, received_at=message.received_at.isoformat() if message.received_at else "",
                        attachment_bytes=attachment.content, attachment_mime=attachment.content_type,
                        thread_context="", known_po_numbers=[],
                    ))
                except GeminiError:
                    LOGGER.error("AI extraction failed for attachment '%s' in message '%s'", attachment.filename, message.message_id, exc_info=True)

            if not ai_results:
                ai_results.append(self.ai.extract(
                    subject=message.subject, body=message.body_text,
                    sender=message.sender, received_at=message.received_at.isoformat() if message.received_at else "",
                    thread_context=thread_context, known_po_numbers=known_po_numbers,
                ))
            ai_result = self._merge_ai_results(ai_results)
        except GeminiError as exc:
            LOGGER.error("AI extraction failed for message '%s': %s", message.message_id, exc, exc_info=True)
            self.db.set_email_status(email_id, "FAILED", str(exc))
            self._remember_in_cache(message)
            return {"status": "FAILED", "reason": str(exc)}

        try:
            result = self.engine.apply_ai_result(ai_result, message, email_id, mailbox.email)
        except Exception as exc:
            LOGGER.error("Saving AI result to database failed for message '%s': %s", message.message_id, exc, exc_info=True)
            self.db.set_email_status(email_id, "FAILED", str(exc))
            self._remember_in_cache(message)
            return {"status": "FAILED", "reason": str(exc)}

        self._remember_in_cache(message)
        return result

    # ------------------------------------------------------------------
    # Mailbox-level processing
    # ------------------------------------------------------------------
    def _config_tz(self):
        try:
            return ZoneInfo(self.config.timezone)
        except Exception:
            LOGGER.warning("Unknown or unavailable timezone '%s' in config; falling back to UTC", self.config.timezone)
            return timezone.utc

    def _localize(self, dt: datetime) -> datetime:
        """Attach the configured timezone to a naive datetime, or leave an
        already-aware datetime as-is (Python compares aware datetimes by
        absolute instant, so no further conversion is needed)."""
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=self._config_tz())

    def _process_mailbox(
        self,
        mailbox: MailboxConfig,
        reprocess_from: datetime | None = None,
    ) -> tuple[int, int, int, list[str]]:
        seen = processed = errors = 0
        error_messages: list[str] = []

        last_processed_iso = self.db.get_last_processed_at(mailbox.email)
        if reprocess_from is not None:
            since_dt = self._localize(reprocess_from)
            LOGGER.info("Mailbox '%s': reprocessing all emails after %s", mailbox.email, since_dt)
        elif last_processed_iso:
            since_dt = self._localize(datetime.fromisoformat(last_processed_iso))
            LOGGER.info("Mailbox '%s': resuming after last processed email at %s", mailbox.email, last_processed_iso)
        else:
            since_dt = self._localize(parse_start_datetime(mailbox.start_datetime))
            LOGGER.info("Mailbox '%s': first run, starting from configured start_datetime %s", mailbox.email, since_dt)

        with self._connect_with_retry(mailbox) as client:
            # IMAP SINCE is date-only and its day boundary may not match the
            # configured timezone exactly, so search a day earlier than needed
            # and rely on the precise timezone-aware filter below to exclude
            # anything not strictly after `since_dt`.
            search_from = since_dt - timedelta(days=1)
            uids = client.search_since_datetime(search_from, self.config.processing.include_seen_messages, self.config.processing.max_emails_per_run)
            LOGGER.info("Mailbox '%s': %s candidate message(s) found on/after %s", mailbox.email, len(uids), since_dt)

            candidates: list[tuple[MailMessage, bytes]] = []
            for uid in uids:
                try:
                    raw = client.fetch_raw_message(uid)
                    message = client.fetch_message(uid)
                except Exception:
                    errors += 1
                    error_messages.append(f"UID {uid!r}: failed to fetch message")
                    LOGGER.error("Mailbox '%s': failed to fetch message UID %r", mailbox.email, uid, exc_info=True)
                    continue

                if message.received_at is None or self._localize(message.received_at) <= since_dt:
                    continue
                if reprocess_from is None and self.db.is_email_read(mailbox.email, message.message_id):
                    continue
                candidates.append((message, raw))


            # Process strictly one email at a time, oldest first, so the resume
            # point always advances in the correct order.
            candidates.sort(key=lambda pair: pair[0].received_at or datetime.min)
            LOGGER.info("Mailbox '%s': %s new email(s) to process this run", mailbox.email, len(candidates))

            for message, raw in candidates:
                seen += 1
                try:
                    result = self._process_one_email(client, mailbox, message, raw)
                    if result.get("status") == "PROCESSED":
                        processed += 1
                    else:
                        errors += 1
                        error_messages.append(f"{message.message_id}: {result.get('reason')}")

                    if self.config.processing.mark_seen and client.conn is not None:
                        client.conn.uid("STORE", message.uid.encode(), "+FLAGS", "(\\Seen)")

                    # A repair run must not move the normal resume checkpoint
                    # backward to an older message.
                    if reprocess_from is None:
                        self.db.set_last_processed(mailbox.email, message.received_at.isoformat(), message.message_id)
                except Exception as exc:
                    errors += 1
                    error_messages.append(f"{message.message_id}: {exc}")
                    LOGGER.error(
                        "Mailbox '%s': unexpected failure processing message '%s'",
                        mailbox.email, message.message_id, exc_info=True,
                    )

        return seen, processed, errors, error_messages

    def run_once(self, reprocess_from: datetime | None = None) -> int:
        run_id = self.db.start_run()
        total_seen = total_processed = errors = 0
        error_messages: list[str] = []
        try:
            for mailbox in self.config.mailboxes:
                if not mailbox.enabled:
                    LOGGER.info("Mailbox disabled: %s (%s)", mailbox.name, mailbox.email)
                    continue
                try:
                    seen, processed, mailbox_errors, mailbox_error_messages = self._process_mailbox(
                        mailbox,
                        reprocess_from=reprocess_from,
                    )
                    total_seen += seen
                    total_processed += processed
                    errors += mailbox_errors
                    error_messages.extend(mailbox_error_messages)
                except Exception as exc:
                    errors += 1
                    error_messages.append(f"Mailbox {mailbox.email}: {exc}")
                    LOGGER.error("Mailbox '%s' (%s) failed: %s", mailbox.name, mailbox.email, exc, exc_info=True)

            status = "SUCCESS" if errors == 0 else "COMPLETED_WITH_ERRORS"
            self.db.finish_run(run_id, status, total_seen, total_processed, errors, " | ".join(error_messages[-20:]))
            LOGGER.info("Run finished: status=%s seen=%s processed=%s errors=%s", status, total_seen, total_processed, errors)
            return 0 if errors == 0 else 2
        except Exception as exc:
            LOGGER.critical("Fatal application run error: %s", exc, exc_info=True)
            self.db.finish_run(run_id, "FAILED", total_seen, total_processed, errors + 1, str(exc))
            return 1
