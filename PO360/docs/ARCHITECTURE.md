# V1 Architecture

## Core principle

Email -> PO -> PO History/Relationships -> Current PO State -> Excel

## Components

- Windows Task Scheduler: starts one processing run every X minutes.
- Mail connector: reads one or more Titan/IMAP mailboxes and normalizes email/attachment data.
- PO candidate rules: cheap deterministic filter to avoid sending obviously irrelevant mail to AI.
- Gemini adapter: extracts structured PO information from email body and supported attachments.
- PO engine: resolves PO number, address context, event type and updates SQLite.
- SQLite: source of truth for emails, attachments, POs, locations, items and events.
- Excel exporter: creates the common report plus history and error sheets.
- Logging: rotating file log plus console output.

## Edge-case handling

1. No fixed PO template: Gemini receives the actual document/body and maps into a fixed normalized schema.
2. Address first, PO later in same thread: address context is stored in `pending_contexts` and thread context is supplied to AI.
3. Multiple POs for same address: PO number is primary identity; address is a location relationship, so one address can have many POs.
4. PO first, address later: later thread message can use the prior PO context; separate PO-number references can also be extracted.
5. PO in body: body-only AI extraction is supported.
6. Cancellation: cancellation is stored as an event and current PO status is changed; history is retained.
7. Revision: revision is stored as an event and latest non-empty fields update the PO/location state.
8. Hold/release: stored as events and current status is updated.
9. Follow-up with same PO PDF: PO number maps it back to the existing PO; it does not create a new PO solely because the PDF was attached again.
10. AI failure: message is logged and marked failed when a PO cannot be identified safely. The application does not invent values.
11. One bad message does not stop the entire mailbox run: failures are isolated per message.
12. Duplicate processing: mailbox + Message-ID is the local idempotency key.
