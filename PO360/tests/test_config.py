from datetime import timezone

from app.core.config import AppConfig, load_config
from app.attachments.processor import save_attachment, save_email_message, save_extracted_json
from app.processing.runner import ApplicationRunner


def test_mark_seen_belongs_to_processing_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        """{\n"""
        "\"processing\": {\"start_date\": \"2026-08-01\", \"mark_seen\": true},"
        "\"mailboxes\": [{\"name\": \"Demo\", \"email\": \"demo@example.com\", \"password\": \"x\", \"imap_host\": \"imap.example.com\"}]"
        "}"
        , encoding="utf-8"
    )
    cfg = load_config(config_path)
    assert cfg.processing.mark_seen is True
    assert not hasattr(cfg.mailboxes[0], "mark_seen")


def test_dev_env_is_supported_and_local_save_works(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"env": "dev", "processing": {"start_date": "2026-08-01"}, "mailboxes": [{"name": "Demo", "email": "demo@example.com", "password": "x", "imap_host": "imap.example.com"}]}',
        encoding="utf-8",
    )

    cfg = load_config(config_path)
    assert cfg.env == "dev"

    email_root = tmp_path / "saved"
    email_path = save_email_message(email_root, "Demo", "msg-123", b"Subject: Hello\n\nBody text")
    attachment_path, _ = save_attachment(email_root, "Demo", "msg-123", "report.pdf", b"PDF-BYTES")

    assert email_path.exists()
    assert attachment_path.exists()
    assert email_path.read_bytes() == b"Subject: Hello\n\nBody text"
    assert attachment_path.read_bytes() == b"PDF-BYTES"

    json_path = save_extracted_json(email_root, "Demo", "msg-123", {"po_number": "PO-123", "is_po_related": True})
    assert json_path.exists()
    assert json_path.read_text(encoding="utf-8")


def test_timezone_falls_back_to_utc_when_zoneinfo_is_unavailable(monkeypatch):
    def raise_missing(key):
        raise Exception(f"No time zone found with key {key}")

    monkeypatch.setattr("app.processing.runner.ZoneInfo", raise_missing)

    runner = object.__new__(ApplicationRunner)
    runner.config = AppConfig(timezone="Asia/Kolkata")

    tz = runner._config_tz()
    assert tz == timezone.utc
