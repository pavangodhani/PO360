"""SQLite persistence layer.

SQLite is the source of truth. The schema below implements the four business
entities required by the PO-tracking pipeline:

  * email_details   - one row per email/thread read (technical + business fields)
  * po_details       - one row per unique PO
  * po_distribution  - one row per delivery location for a PO (many per PO)
  * po_logs          - one row per new/unique email communication about a PO

Supporting tables:
  * raw_attachments      - attachment metadata/paths for each email
  * mailbox_state        - per-mailbox "resume from here" bookkeeping
  * pending_po_context   - address/branch info seen before a PO number is known
  * processing_runs      - per-run summary/audit trail
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS email_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    primary_email TEXT NOT NULL,
    message_id TEXT NOT NULL,
    thread_id TEXT,
    subject TEXT,
    sender_email TEXT,
    to_recipients TEXT,
    cc_recipients TEXT,
    received_at TEXT,
    body_text TEXT,
    body_html TEXT,
    attachment_count INTEGER NOT NULL DEFAULT 0,
    attachment_names TEXT,
    is_po_related INTEGER,
    po_remarks TEXT,
    po_confidence INTEGER,
    raw_saved_path TEXT,
    processing_status TEXT NOT NULL DEFAULT 'NEW',
    processing_error TEXT,
    processed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(primary_email, message_id)
);

CREATE TABLE IF NOT EXISTS raw_attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_id INTEGER NOT NULL REFERENCES email_details(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content_type TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    path TEXT,
    sha256 TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(email_id, filename, sha256)
);

CREATE TABLE IF NOT EXISTS po_details (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number TEXT NOT NULL UNIQUE,
    company_name TEXT,
    sent_by TEXT,
    po_datetime TEXT,
    total_goods TEXT,
    thread_id TEXT,
    primary_email TEXT,
    source_email_id INTEGER REFERENCES email_details(id) ON DELETE SET NULL,
    last_source_received_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS po_distribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number TEXT NOT NULL REFERENCES po_details(po_number) ON DELETE CASCADE,
    address_date TEXT,
    address TEXT NOT NULL DEFAULT '',
    goods TEXT,
    state_region TEXT,
    branch_name TEXT,
    branch_code TEXT,
    branch_manager_name TEXT,
    branch_manager_contact TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    source_email_id INTEGER REFERENCES email_details(id) ON DELETE SET NULL,
    last_source_received_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(po_number, address)
);

CREATE TABLE IF NOT EXISTS po_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number TEXT NOT NULL REFERENCES po_details(po_number) ON DELETE CASCADE,
    email_date TEXT,
    email_sender TEXT,
    email_subject TEXT,
    email_conclusion TEXT,
    source_email_id INTEGER REFERENCES email_details(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    UNIQUE(po_number, source_email_id)
);

CREATE TABLE IF NOT EXISTS pending_po_context (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    primary_email TEXT NOT NULL,
    thread_id TEXT,
    address_date TEXT,
    address TEXT,
    goods TEXT,
    state_region TEXT,
    branch_name TEXT,
    branch_code TEXT,
    branch_manager_name TEXT,
    branch_manager_contact TEXT,
    company_name TEXT,
    source_email_id INTEGER REFERENCES email_details(id) ON DELETE SET NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mailbox_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    primary_email TEXT NOT NULL UNIQUE,
    last_processed_at TEXT,
    last_message_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processing_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    emails_seen INTEGER NOT NULL DEFAULT 0,
    emails_processed INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_email_details_received_at ON email_details(received_at);
CREATE INDEX IF NOT EXISTS idx_email_details_thread_id ON email_details(thread_id);
CREATE INDEX IF NOT EXISTS idx_po_distribution_po_number ON po_distribution(po_number);
CREATE INDEX IF NOT EXISTS idx_po_logs_po_number ON po_logs(po_number);
CREATE INDEX IF NOT EXISTS idx_pending_po_context_thread ON pending_po_context(primary_email, thread_id, resolved);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _time_ordered_update_clause(table: str, fields: list[str], ts_field: str) -> str:
    """Build an "overwrite when new value is non-empty, else keep old" SET clause,
    guarded against out-of-order writes.

    The same PO is often reported from more than one mailbox, and each mailbox
    is processed in its own oldest-first order within a run - so a mailbox
    with an older backlog can be written to the database *after* another
    mailbox's newer email. Without a time check, that older email would
    overwrite newer business data with stale values.

    `ts_field` (last_source_received_at) records the received-at time of the
    email that last updated the row. An incoming write only wins when either
    side lacks timing info (best effort, preserves old behaviour), or the
    incoming timestamp is not older than what is already stored.
    """
    guard = (
        f"(excluded.{ts_field} IS NULL OR {table}.{ts_field} IS NULL "
        f"OR excluded.{ts_field} >= {table}.{ts_field})"
    )
    parts = []
    for field in fields:
        parts.append(
            f"{field}=CASE WHEN {guard} AND excluded.{field} IS NOT NULL AND excluded.{field} != '' "
            f"THEN excluded.{field} ELSE {table}.{field} END"
        )
    parts.append(
        f"{ts_field}=CASE WHEN {guard} AND excluded.{ts_field} IS NOT NULL "
        f"THEN excluded.{ts_field} ELSE {table}.{ts_field} END"
    )
    return ", ".join(parts)


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add columns introduced after the initial schema to pre-existing databases."""
        for table, column, ddl in (
            ("po_details", "last_source_received_at", "ALTER TABLE po_details ADD COLUMN last_source_received_at TEXT"),
            ("po_distribution", "last_source_received_at", "ALTER TABLE po_distribution ADD COLUMN last_source_received_at TEXT"),
        ):
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.execute(ddl)
        conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # mailbox_state - "resume from here" bookkeeping
    # ------------------------------------------------------------------
    def get_last_processed_at(self, primary_email: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT last_processed_at FROM mailbox_state WHERE primary_email=?",
                (primary_email,),
            ).fetchone()
            return row["last_processed_at"] if row else None

    def set_last_processed(self, primary_email: str, received_at: str | None, message_id: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO mailbox_state(primary_email,last_processed_at,last_message_id,updated_at)
                VALUES(?,?,?,?)
                ON CONFLICT(primary_email) DO UPDATE SET
                last_processed_at=excluded.last_processed_at,
                last_message_id=excluded.last_message_id,
                updated_at=excluded.updated_at""",
                (primary_email, received_at, message_id, utc_now()),
            )

    # ------------------------------------------------------------------
    # email_details
    # ------------------------------------------------------------------
    def is_email_read(self, primary_email: str, message_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM email_details WHERE primary_email=? AND message_id=?",
                (primary_email, message_id),
            ).fetchone()
            return row is not None

    def upsert_email_details(self, email: dict[str, Any]) -> int:
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO email_details(
                    primary_email,message_id,thread_id,subject,sender_email,to_recipients,cc_recipients,
                    received_at,body_text,body_html,attachment_count,attachment_names,
                    is_po_related,po_remarks,po_confidence,raw_saved_path,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(primary_email,message_id) DO UPDATE SET
                    thread_id=excluded.thread_id, subject=excluded.subject, sender_email=excluded.sender_email,
                    to_recipients=excluded.to_recipients, cc_recipients=excluded.cc_recipients,
                    received_at=excluded.received_at, body_text=excluded.body_text, body_html=excluded.body_html,
                    attachment_count=excluded.attachment_count, attachment_names=excluded.attachment_names,
                    is_po_related=excluded.is_po_related, po_remarks=excluded.po_remarks,
                    po_confidence=excluded.po_confidence, raw_saved_path=excluded.raw_saved_path,
                    updated_at=excluded.updated_at""",
                (
                    email["primary_email"], email["message_id"], email.get("thread_id"), email.get("subject"),
                    email.get("sender_email"), email.get("to_recipients"), email.get("cc_recipients"),
                    email.get("received_at"), email.get("body_text"), email.get("body_html"),
                    email.get("attachment_count", 0), email.get("attachment_names"),
                    email.get("is_po_related"), email.get("po_remarks"), email.get("po_confidence"),
                    email.get("raw_saved_path"), now, now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM email_details WHERE primary_email=? AND message_id=?",
                (email["primary_email"], email["message_id"]),
            ).fetchone()
            return int(row[0])

    def set_email_status(self, email_id: int, status: str, error: str | None = None) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE email_details SET processing_status=?, processing_error=?, processed_at=?, updated_at=? WHERE id=?",
                (status, error, utc_now() if status in {"PROCESSED", "FAILED", "SKIPPED"} else None, utc_now(), email_id),
            )

    def add_raw_attachment(self, email_id: int, attachment: dict[str, Any]) -> int:
        with self.transaction() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO raw_attachments(email_id,filename,content_type,size_bytes,path,sha256,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    email_id, attachment["filename"], attachment.get("content_type"), attachment.get("size_bytes", 0),
                    attachment.get("path"), attachment.get("sha256"), utc_now(),
                ),
            )
            row = conn.execute(
                "SELECT id FROM raw_attachments WHERE email_id=? AND filename=? AND sha256=?",
                (email_id, attachment["filename"], attachment.get("sha256")),
            ).fetchone()
            return int(row[0])

    def get_thread_emails(self, primary_email: str, thread_id: str | None, limit: int = 25) -> list[sqlite3.Row]:
        """Return prior emails in the same thread, oldest first, for AI context."""
        if not thread_id:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT message_id, subject, sender_email, received_at, body_text, po_remarks
                FROM email_details WHERE primary_email=? AND thread_id=?
                ORDER BY received_at DESC LIMIT ?""",
                (primary_email, thread_id, limit),
            ).fetchall()
            return list(reversed(rows))

    def get_known_po_numbers_for_thread(self, thread_id: str | None) -> list[str]:
        if not thread_id:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT po_number FROM po_details WHERE thread_id=?",
                (thread_id,),
            ).fetchall()
            return [r["po_number"] for r in rows]

    # ------------------------------------------------------------------
    # po_details
    # ------------------------------------------------------------------
    def upsert_po_details(self, po: dict[str, Any]) -> int:
        """Insert or merge a PO's business details.

        `po_number` is the sole natural key (not scoped by mailbox), so the
        same PO arriving via two different business email accounts merges
        into one row instead of creating a duplicate. `last_source_received_at`
        (the triggering email's received-at time) guards against an
        out-of-order write (e.g. a different mailbox's older backlog
        processed later in the same run) clobbering newer data.
        """
        now = utc_now()
        with self.transaction() as conn:
            conn.execute(
                f"""INSERT INTO po_details(po_number,company_name,sent_by,po_datetime,total_goods,thread_id,
                    primary_email,source_email_id,last_source_received_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(po_number) DO UPDATE SET
                {_time_ordered_update_clause('po_details', ['company_name', 'sent_by', 'po_datetime', 'total_goods'], 'last_source_received_at')},
                thread_id=COALESCE(po_details.thread_id, excluded.thread_id),
                primary_email=COALESCE(po_details.primary_email, excluded.primary_email),
                source_email_id=COALESCE(po_details.source_email_id, excluded.source_email_id),
                updated_at=excluded.updated_at""",
                (
                    po["po_number"], po.get("company_name") or "", po.get("sent_by") or "",
                    po.get("po_datetime") or "", po.get("total_goods") or "", po.get("thread_id"),
                    po.get("primary_email"), po.get("source_email_id"), po.get("last_source_received_at"), now, now,
                ),
            )
            row = conn.execute("SELECT id FROM po_details WHERE po_number=?", (po["po_number"],)).fetchone()
            return int(row[0])

    def get_po_details(self, po_number: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM po_details WHERE po_number=?", (po_number,)).fetchone()

    # ------------------------------------------------------------------
    # po_distribution
    # ------------------------------------------------------------------
    def upsert_po_distribution(self, distribution: dict[str, Any]) -> int:
        """Insert or update a delivery location for a PO.

        `status` is intentionally NOT part of this general upsert: a plain
        info update (e.g. a corrected contact number) must never silently
        clear an existing ON_HOLD status. Use `set_po_distribution_status`
        for hold/release notices instead. `last_source_received_at` guards
        against an out-of-order write (e.g. a different mailbox's older
        backlog processed later in the same run) overwriting newer data.
        """
        now = utc_now()
        address = (distribution.get("address") or "").strip()
        with self.transaction() as conn:
            conn.execute(
                f"""INSERT INTO po_distribution(po_number,address_date,address,goods,state_region,branch_name,
                    branch_code,branch_manager_name,branch_manager_contact,source_email_id,last_source_received_at,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(po_number,address) DO UPDATE SET
                {_time_ordered_update_clause('po_distribution', ['address_date', 'goods', 'state_region', 'branch_name', 'branch_code', 'branch_manager_name', 'branch_manager_contact'], 'last_source_received_at')},
                source_email_id=COALESCE(excluded.source_email_id, po_distribution.source_email_id),
                updated_at=excluded.updated_at""",
                (
                    distribution["po_number"], distribution.get("address_date") or "", address,
                    distribution.get("goods") or "", distribution.get("state_region") or "",
                    distribution.get("branch_name") or "", distribution.get("branch_code") or "",
                    distribution.get("branch_manager_name") or "", distribution.get("branch_manager_contact") or "",
                    distribution.get("source_email_id"), distribution.get("last_source_received_at"), now, now,
                ),
            )
            row = conn.execute(
                "SELECT id FROM po_distribution WHERE po_number=? AND address=?",
                (distribution["po_number"], address),
            ).fetchone()
            return int(row[0])

    def set_po_distribution_status(self, po_number: str, address: str, status: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE po_distribution SET status=?, updated_at=? WHERE po_number=? AND address=?",
                (status, utc_now(), po_number, (address or "").strip()),
            )

    def delete_source_po_distributions(self, source_email_id: int, po_numbers: list[str]) -> None:
        """Remove stale distribution rows written from one reprocessed email."""
        if not po_numbers:
            return
        placeholders = ", ".join("?" for _ in po_numbers)
        with self.transaction() as conn:
            conn.execute(
                f"DELETE FROM po_distribution WHERE source_email_id=? AND po_number IN ({placeholders})",
                [source_email_id, *po_numbers],
            )

    def get_po_distributions(self, po_number: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM po_distribution WHERE po_number=?", (po_number,)).fetchall()

    # ------------------------------------------------------------------
    # po_logs
    # ------------------------------------------------------------------
    def add_po_log(self, log: dict[str, Any]) -> int:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO po_logs(po_number,email_date,email_sender,email_subject,email_conclusion,source_email_id,created_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(po_number,source_email_id) DO UPDATE SET
                email_conclusion=excluded.email_conclusion""",
                (
                    log["po_number"], log.get("email_date"), log.get("email_sender"), log.get("email_subject"),
                    log.get("email_conclusion"), log.get("source_email_id"), utc_now(),
                ),
            )
            row = conn.execute(
                "SELECT id FROM po_logs WHERE po_number=? AND source_email_id=?",
                (log["po_number"], log.get("source_email_id")),
            ).fetchone()
            return int(row[0]) if row else -1

    # ------------------------------------------------------------------
    # pending_po_context - address/branch info seen before a PO number is known
    # ------------------------------------------------------------------
    def add_pending_po_context(self, context: dict[str, Any]) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO pending_po_context(primary_email,thread_id,address_date,address,goods,state_region,
                    branch_name,branch_code,branch_manager_name,branch_manager_contact,company_name,source_email_id,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    context["primary_email"], context.get("thread_id"), context.get("address_date"),
                    context.get("address"), context.get("goods"), context.get("state_region"),
                    context.get("branch_name"), context.get("branch_code"), context.get("branch_manager_name"),
                    context.get("branch_manager_contact"), context.get("company_name"), context.get("source_email_id"),
                    utc_now(),
                ),
            )
            return int(cur.lastrowid)

    def get_pending_po_context(self, primary_email: str, thread_id: str | None) -> list[sqlite3.Row]:
        if not thread_id:
            return []
        with self.connect() as conn:
            return conn.execute(
                """SELECT * FROM pending_po_context WHERE primary_email=? AND thread_id=? AND resolved=0
                ORDER BY created_at""",
                (primary_email, thread_id),
            ).fetchall()

    def mark_pending_po_context_resolved(self, ids: list[int]) -> None:
        if not ids:
            return
        with self.transaction() as conn:
            conn.executemany("UPDATE pending_po_context SET resolved=1 WHERE id=?", [(i,) for i in ids])

    # ------------------------------------------------------------------
    # processing_runs
    # ------------------------------------------------------------------
    def start_run(self) -> int:
        with self.transaction() as conn:
            cur = conn.execute("INSERT INTO processing_runs(started_at,status) VALUES(?,?)", (utc_now(), "RUNNING"))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, seen: int, processed: int, errors: int, summary: str | None = None) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE processing_runs SET finished_at=?,status=?,emails_seen=?,emails_processed=?,errors=?,error_summary=? WHERE id=?",
                (utc_now(), status, seen, processed, errors, summary, run_id),
            )

    # ------------------------------------------------------------------
    # Excel export - date-window queries over the four business entities.
    # See app/reporting/data_export.py for the assumed "relevant date field"
    # per entity (email_details.received_at, po_details/po_distribution
    # .updated_at, po_logs.email_date).
    # ------------------------------------------------------------------
    def export_email_details(self, from_iso: str, to_iso: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM email_details WHERE received_at BETWEEN ? AND ? ORDER BY received_at",
                (from_iso, to_iso),
            ).fetchall()

    def export_po_details(self, from_iso: str, to_iso: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM po_details WHERE updated_at BETWEEN ? AND ? ORDER BY updated_at",
                (from_iso, to_iso),
            ).fetchall()

    def export_po_distribution(self, from_iso: str, to_iso: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM po_distribution WHERE updated_at BETWEEN ? AND ? ORDER BY updated_at",
                (from_iso, to_iso),
            ).fetchall()

    def export_po_logs(self, from_iso: str, to_iso: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM po_logs WHERE email_date BETWEEN ? AND ? ORDER BY email_date",
                (from_iso, to_iso),
            ).fetchall()
