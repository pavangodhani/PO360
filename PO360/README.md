# Outlook Email Analyzer - PO Analyzer V1

This is a Windows-first monolithic application for the first client demo. It reads configured mailboxes over IMAP, identifies PO-related messages, uses Gemini for document/body understanding, stores PO history in SQLite, and generates a standardized Excel workbook.

## Important scope

This build is intentionally conservative. It does **not** claim 100% automatic correctness. When a PO candidate cannot be reliably identified, it records a failure and preserves the source email so the issue can be diagnosed. It never intentionally invents missing values.

## Requirements

- Windows 10/11
- Python 3.12.x
- Internet access for IMAP and Gemini API
- A Titan/IMAP-capable mailbox (exact Titan server details must be supplied in config)
- Gemini API key for the demo

## Quick start on Windows

1. Install Python 3.12 from python.org. During installation enable **Add Python to PATH**.
2. Extract this project folder to a local path such as `C:\OutlookEmailAnalyzer`.
3. Open Command Prompt in that folder.
4. Run `scripts\\install.bat`.
5. Edit `config\\config.json`.
6. Put the test mailbox email/password and the correct Titan IMAP host/port into the mailbox section.
7. Put the Gemini demo API key into `ai.api_key`.
8. Run `scripts\\init.bat`.
9. Run `scripts\\run_once.bat` for a manual test.
10. Open `output\\PO_Report.xlsx`.
11. To watch logs, run `scripts\\monitor_logs.bat` in another Command Prompt window.
12. Only after manual testing works, run `scripts\\setup_scheduler.bat` and choose the desired interval.

## First test recommendation

Do not start with the client's real mailbox. Use the dummy/test mailbox discussed for the first demo. Start with a very small set of emails and one or two PO PDFs.

## Configuration

`config\\config.example.json` is the template. `config\\config.json` is local and should never be committed to source control.

The key fields are:

- `processing.start_date`: first date to search from
- `processing.interval_minutes`: intended scheduler interval
- `processing.max_emails_per_run`: safety cap
- `output.excel_path`: report path
- `ai.api_key`: Gemini key
- `ai.model`: Gemini model name; keep this configurable because model availability can change
- `mailboxes`: one or more mailbox configurations

## Titan IMAP settings

You confirmed that the client uses Titan and that the same email/password work in Outlook. Outlook being able to send/receive does not by itself tell us the IMAP hostname. Obtain the Titan IMAP server/port/authentication settings from the mailbox provider or the client's existing Outlook account settings and put them into `config.json`.

## Files produced

- `data/analyzer.db` - SQLite source of truth
- `data/attachments/` - retained PO attachments
- `output/PO_Report.xlsx` - standardized business report
- `logs/outlook_analyzer.log` - rotating application log

## Excel workbook

The workbook contains:

1. **PO Report** - standardized master business view
2. **PO History** - PO events and source email information
3. **Processing Errors** - failed messages and error details

The PO Report is based on the four supplied Numbers examples and uses a common superset schema. Missing/non-applicable values are left blank.

## Error reporting

If something fails, send:

1. The relevant lines from `logs\\outlook_analyzer.log`.
2. The `Processing Errors` sheet from `output\\PO_Report.xlsx`.
3. The exact command you ran.
4. The Windows/Python version.
5. If relevant, the mailbox configuration **with the password/API key removed**.

Never send a real mailbox password or Gemini API key.

## Security note for V1

The first demo intentionally supports email/password in the local JSON configuration because the application runs on the controlled client machine. This is a temporary V1 decision. The credential access is isolated behind the mail configuration boundary so it can later be migrated to Windows Credential Manager/OAuth without rewriting the PO processing engine.

## Scheduler note

`setup_scheduler.bat` creates a Windows Scheduled Task that runs `scripts\\run_once.bat`. The scheduled runner must not contain `pause`, because a scheduled task would otherwise wait indefinitely.

## Build verification

The 1.0.2 package was verified with 12 automated tests and Python bytecode compilation before packaging. Tests can be run from the project root with `pytest -q`.
