from app.database.db import Database


def test_email_details_upsert(tmp_path):
    db = Database(tmp_path / "test.db")
    email_id = db.upsert_email_details({
        "primary_email": "demo@example.com", "message_id": "1", "thread_id": "t1",
        "subject": "PO Update", "sender_email": "vendor@abc.com", "to_recipients": "demo@example.com",
        "cc_recipients": "", "received_at": "2026-08-10T14:10:00+05:30", "body_text": "body",
        "body_html": "", "attachment_count": 1, "attachment_names": "po.pdf",
    })
    assert email_id > 0
    assert db.is_email_read("demo@example.com", "1") is True

    db.upsert_email_details({
        "primary_email": "demo@example.com", "message_id": "1", "thread_id": "t1",
        "subject": "PO Update", "sender_email": "vendor@abc.com", "to_recipients": "demo@example.com",
        "cc_recipients": "", "received_at": "2026-08-10T14:10:00+05:30", "body_text": "body",
        "body_html": "", "attachment_count": 1, "attachment_names": "po.pdf",
        "is_po_related": 1, "po_remarks": "Contains a PO", "po_confidence": 9,
    })
    rows = db.get_thread_emails("demo@example.com", "t1")
    assert len(rows) == 1
    assert rows[0]["po_remarks"] == "Contains a PO"


def test_mailbox_state_resume(tmp_path):
    db = Database(tmp_path / "test.db")
    assert db.get_last_processed_at("demo@example.com") is None
    db.set_last_processed("demo@example.com", "2026-08-10T14:10:00", "msg-1")
    assert db.get_last_processed_at("demo@example.com") == "2026-08-10T14:10:00"


def test_po_details_and_distribution_upsert_prefers_new_nonempty_value(tmp_path):
    db = Database(tmp_path / "test.db")
    db.upsert_po_details({"po_number": "PO-100", "company_name": "ABC", "po_datetime": "2026-08-10T10:00:00"})
    db.upsert_po_details({"po_number": "PO-100", "company_name": "ABC Corp Updated"})
    po = db.get_po_details("PO-100")
    assert po["company_name"] == "ABC Corp Updated"

    db.upsert_po_distribution({"po_number": "PO-100", "address": "Pune", "branch_manager_contact": "111"})
    db.upsert_po_distribution({"po_number": "PO-100", "address": "Pune", "branch_manager_contact": "222"})
    rows = db.get_po_distributions("PO-100")
    assert len(rows) == 1
    assert rows[0]["branch_manager_contact"] == "222"


def test_po_distribution_hold_status_is_not_cleared_by_unrelated_update(tmp_path):
    db = Database(tmp_path / "test.db")
    db.upsert_po_details({"po_number": "PO-200"})
    db.upsert_po_distribution({"po_number": "PO-200", "address": "Nagpur", "goods": "Fan (5)"})
    db.set_po_distribution_status("PO-200", "Nagpur", "ON_HOLD")
    db.upsert_po_distribution({"po_number": "PO-200", "address": "Nagpur", "branch_manager_contact": "999"})
    row = db.get_po_distributions("PO-200")[0]
    assert row["status"] == "ON_HOLD"
    assert row["branch_manager_contact"] == "999"


def test_pending_po_context_resolution(tmp_path):
    db = Database(tmp_path / "test.db")
    db.add_pending_po_context({"primary_email": "demo@example.com", "thread_id": "t1", "address": "Mumbai Site"})
    rows = db.get_pending_po_context("demo@example.com", "t1")
    assert len(rows) == 1
    db.mark_pending_po_context_resolved([rows[0]["id"]])
    assert db.get_pending_po_context("demo@example.com", "t1") == []


def test_po_logs(tmp_path):
    db = Database(tmp_path / "test.db")
    db.upsert_po_details({"po_number": "PO-300"})
    email_id = db.upsert_email_details({"primary_email": "demo@example.com", "message_id": "1"})
    db.add_po_log({
        "po_number": "PO-300", "email_date": "2026-08-10T10:00:00", "email_sender": "vendor@abc.com",
        "email_subject": "PO-300", "email_conclusion": "Shared the PO", "source_email_id": email_id,
    })
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM po_logs WHERE po_number='PO-300'").fetchall()
    assert len(rows) == 1
    assert rows[0]["email_conclusion"] == "Shared the PO"
