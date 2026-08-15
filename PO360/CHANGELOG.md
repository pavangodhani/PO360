# Changelog

## 1.0.2 - 2026-08-11

- Fixed a production-blocking configuration bug where `mark_seen` was incorrectly read from `MailboxConfig` instead of `ProcessingConfig`.
- IMAP client now receives the `mark_seen` behavior explicitly instead of coupling mailbox credentials/configuration to processing behavior.
- Added explicit validation when marking an IMAP message as seen.
- Added regression tests for the reported configuration bug and IMAP `mark_seen` behavior.
- Added `pytest.ini` so tests run correctly from the project root without manually setting `PYTHONPATH`.
- Verified with `pytest -q`: 12 tests passing.
- Verified Python compilation with `python -m compileall`: successful.
