# V1 Test Plan

Run automated tests with:

```bat
.venv\Scripts\python.exe -m pytest -q
```

The test suite currently covers:

- PO number extraction
- PO candidate rules
- event normalization
- SQLite PO/location/item persistence
- pending address context
- one PO with multiple locations
- follow-up/re-attached PO mapping
- cancellation status update
- Excel column count/report generation

Manual demo scenarios to run before connecting a real mailbox:

1. PO PDF in attachment.
2. PO details in email body only.
3. Address email before PO in the same thread.
4. PO first and address later.
5. Same address with two different PO numbers.
6. Revision email in same thread.
7. Revision email in another thread with PO number.
8. Cancellation in same thread.
9. Cancellation in another thread with PO number.
10. Hold/release sequence.
11. Follow-up email that re-attaches the same PO PDF.
12. Same PO number covering multiple addresses.
13. Corrupt/password-protected attachment.
14. Gemini API quota/timeout failure.
15. One bad email among otherwise valid emails.
16. Scheduler running twice without duplicate PO rows.
