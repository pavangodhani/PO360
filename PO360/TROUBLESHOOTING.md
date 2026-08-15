# Troubleshooting

## 1. `IMAP login failed`

Check:
- email address
- password
- IMAP host
- IMAP port (normally 993 for SSL, but use the provider's actual setting)
- whether IMAP access is enabled for the mailbox

Do not assume the webmail URL is the IMAP hostname.

## 2. `Gemini HTTP 400`

Usually check:
- configured model name
- API key validity
- whether the selected model accepts the supplied input type
- whether the request is too large

The full HTTP response is written to the log (with a bounded response excerpt). Do not paste API keys into support messages.

## 3. `Gemini HTTP 429`

This means the API rejected the request due to rate/quota limits. The application retries transient errors. If the free demo quota is exhausted, reduce the test volume or replace the API key/model as appropriate.

## 4. PDF processing error

Check whether the attachment is a real PDF and whether it is password-protected/corrupt. The application keeps the original attachment under `data\\attachments` when retention is enabled.

## 5. Excel file cannot be opened

Close the workbook in Excel before running the exporter. Windows/Excel can lock an output file while the application is trying to replace it.

## 6. A PO appears twice

Check whether the PO has multiple addresses. The data model intentionally allows one PO number to have multiple address/location rows. Also check `PO History` to see whether the duplicate is actually a revision/follow-up event.

## 7. Address arrived before PO

The application stores address-only context in SQLite under `pending_contexts`. When a later PO arrives in the same thread, that context can be used to populate the PO location.

## 8. What to send ChatGPT for debugging

Paste the relevant log section, for example:

```text
2026-08-11 21:10:00 | ERROR | ... | message...
Traceback (most recent call last):
...
```

Also provide the `Processing Errors` row if one exists. Redact:
- mailbox password
- Gemini API key
- confidential customer information
- full email body if it contains sensitive information
