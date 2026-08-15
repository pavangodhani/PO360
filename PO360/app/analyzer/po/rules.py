"""Deterministic PO clues.

Rules are deliberately conservative. They do not claim an email is a PO just
because the word 'order' appears; they identify useful candidates for deeper
analysis and provide a non-AI fallback for obvious PO-number references.
"""
from __future__ import annotations

import re

PO_NUMBER_PATTERNS = [
    re.compile(r"\bPO\s*(?:NO|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/_\-.]{4,})\b", re.I),
    re.compile(r"\bP\.O\.\s*(?:NO|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/_\-.]{4,})\b", re.I),
]
KEYWORDS = re.compile(r"\b(purchase\s*order|purchase\s*ord|\bPO\b|P\.O\.|order\s*(?:no|number)|po\s*number|cancel(?:led|lation)?|on\s*hold|release|revise|revision)\b", re.I)
ADDRESS_CLUES = re.compile(r"\b(address|pin(?:code)?|pincode|postal|road|street|district|state|branch|location|city|village)\b", re.I)


def extract_po_number(text: str) -> str | None:
    for pattern in PO_NUMBER_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return match.group(1).strip(".,;:)")
    return None


def normalize_po_number(value: str | None) -> str:
    """Canonicalize a PO number so the same PO is recognized across mailboxes/emails.

    Different mailboxes/AI passes may render the same PO number with different
    case or spacing (e.g. "po-12345" vs "PO-12345"). This is used as the
    lookup/storage key everywhere so such variants merge into one PO record
    instead of creating duplicates. It intentionally does not strip internal
    separators like "-", "/" or "_" because those can be a meaningful part of
    the number (e.g. "PO-2026-001").
    """
    return re.sub(r"\s+", " ", (value or "").strip()).upper()


def is_candidate(subject: str, body: str, attachment_names: list[str]) -> bool:
    combined = " ".join([subject or "", body or "", *attachment_names])
    if KEYWORDS.search(combined):
        return True
    if ADDRESS_CLUES.search(combined):
        # Address-before-PO is a known business case. This deliberately uses
        # several address clues rather than the word "address" alone, reducing
        # noise while allowing legitimate address-only messages through.
        clue_count = len(ADDRESS_CLUES.findall(combined))
        if clue_count >= 2 or re.search(r"\b\d{5,6}\b", combined):
            return True
    return any(re.search(r"\bpo\b", name, re.I) for name in attachment_names)


def normalize_event_type(value: str | None) -> str:
    allowed = {"NEW", "ADDRESS_RECEIVED", "REVISION", "CANCELLATION", "HOLD", "RELEASE", "FOLLOW_UP", "UPDATE", "DELIVERY_STATUS", "INVOICE_STATUS", "OTHER"}
    value = (value or "OTHER").strip().upper().replace(" ", "_")
    aliases = {"CANCELLED": "CANCELLATION", "CANCEL": "CANCELLATION", "REVISED": "REVISION", "ON_HOLD": "HOLD", "RELEASED": "RELEASE", "FOLLOWUP": "FOLLOW_UP"}
    value = aliases.get(value, value)
    return value if value in allowed else "OTHER"
