"""PO business logic: turns a Gemini extraction result into database rows.

This module owns the mapping from the AI's per-email JSON result into the
four business entities (email_details classification fields, po_details,
po_distribution, po_logs), including the "link new info back to an existing
PO in the same thread" behaviour described in the requirements.
"""
from __future__ import annotations

import logging
import re
from datetime import timezone
from typing import Any

from app.analyzer.po.rules import extract_po_number, normalize_po_number
from app.database.db import Database
from app.mail.models import MailMessage

LOGGER = logging.getLogger(__name__)

_PO_NUMBER_RE = re.compile(r"LTFH/PO/[A-Z0-9/_-]+", re.IGNORECASE)
_MC_CODE_RE = re.compile(r"\b(?:MC|BRANCH|CLUSTER)\s*\d+[A-Z0-9-]*\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(?:\+91[- ]?)?\d{10}\b")
_INDIAN_STATES = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat",
    "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh",
    "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan",
    "Sikkim", "Tamil Nadu", "Telangana", "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal",
    "Delhi", "Jammu and Kashmir", "Ladakh",
)


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip(" ,")


def _state_from_address(address: str) -> str:
    for state in _INDIAN_STATES:
        if re.search(rf"\b{re.escape(state)}\b(?=\s*,|\s+\d{{6}}\b|$)", address or "", re.IGNORECASE):
            return state
    return ""


def _is_valid_state_region(value: str) -> bool:
    """Validate that a state_region value is a legitimate Indian state/region.
    
    Filters out:
    - MC/MC- codes (e.g., "MC-01", "MC-5")
    - Territory markers (e.g., "Territory-North", "Terr-South")
    - Branch/cluster codes (e.g., "Branch-A", "Cluster-1")
    - Abbreviations (e.g., "MH", "TN", "DL")
    - Location codes and numbered locations
    - Empty or purely numeric values
    
    Returns True only if the value matches a known Indian state name.
    """
    if not value or not isinstance(value, str):
        return False
    
    value_stripped = value.strip()
    
    # Reject common invalid patterns
    invalid_patterns = [
        r"^mc[\s-]?\d+",  # MC-01, MC 5, etc.
        r"^territory[\s-]",  # Territory-North, etc.
        r"^terr[\s-]",  # Abbreviation of territory
        r"^branch[\s-]",  # Branch-A, Branch-1, etc.
        r"^cluster[\s-]",  # Cluster-1, etc.
        r"^location[\s-]",  # Location-1, etc.
        r"^l[\s-]?\d+",  # L-01, L 5, etc.
        r"^zone[\s-]",  # Zone-North, etc.
        r"^region[\s-]",  # Region-South, etc.
        r"^\d+$",  # Pure numbers
        r"^[a-z]{1,2}[\s-]?\d+$",  # Abbreviations with numbers (MH-01, TN-5, etc.)
    ]
    
    for pattern in invalid_patterns:
        if re.search(pattern, value_stripped, re.IGNORECASE):
            return False
    
    # Only valid if it matches a known Indian state
    for state in _INDIAN_STATES:
        if value_stripped.lower() == state.lower():
            return True
    
    return False


def _email_distribution_map(body: str) -> dict[str, dict[str, str]]:
    """Parse a flattened MC/PO/address/contact table using PO number as key."""
    lines = [_clean_line(line) for line in (body or "").splitlines()]
    lines = [line for line in lines if line]
    result: dict[str, dict[str, str]] = {}

    for index, line in enumerate(lines):
        po_match = _PO_NUMBER_RE.fullmatch(line)
        if not po_match or index < 2:
            continue
        phone_index = next(
            (candidate for candidate in range(index + 1, min(index + 8, len(lines))) if _PHONE_RE.fullmatch(lines[candidate])),
            None,
        )
        if phone_index is None or phone_index <= index + 2:
            continue
        address = _clean_line(" ".join(lines[index + 1:phone_index - 1]))
        code_match = _MC_CODE_RE.search(lines[index - 2])
        result[normalize_po_number(po_match.group(0))] = {
            "address": address,
            "state_region": _state_from_address(address),
            "branch_name": lines[index - 1],
            "branch_code": code_match.group(0).upper().replace(" ", "") if code_match else "",
            "branch_manager_name": lines[phone_index - 1],
            "branch_manager_contact": lines[phone_index],
        }
    return result


def _received_at_utc_iso(message: MailMessage) -> str:
    """UTC-normalized received-at time, used only to order cross-mailbox merges.

    Different mailboxes/timezones can report `received_at` with different UTC
    offsets; converting to UTC before comparing timestamps (see
    `Database._time_ordered_update_clause`) avoids incorrectly treating an
    offset difference as an out-of-order write.
    """
    if not message.received_at:
        return ""
    dt = message.received_at
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


def _detect_equal_distribution_count(text: str) -> int:
    """Detect if text mentions equal distribution across N branches/MCs.
    
    Returns the count of branches if found (e.g., "PO for 5 MCs" returns 5),
    or 0 if not found.
    """
    if not text:
        return 0
    # Look for patterns like "5 MCs", "5 branches", "all 5", etc.
    patterns = [
        r"\b(\d+)\s*(?:MC|MCs|branch|branches|location|locations)\b",
        r"\bfor\s+(\d+)\s*(?:unit|units|place|places)",
        r"\bequally\s+to\s+(\d+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, AttributeError):
                pass
    return 0


class POEngine:
    def __init__(self, db: Database):
        self.db = db

    def known_po_numbers(self, thread_id: str | None) -> list[str]:
        return self.db.get_known_po_numbers_for_thread(thread_id)

    def build_thread_context_text(self, rows: list[Any]) -> str:
        """Render prior thread emails (DB rows or dict-like) as AI context text."""
        pieces = []
        for r in rows:
            subject = r["subject"] if "subject" in r.keys() else ""
            sender = r["sender_email"] if "sender_email" in r.keys() else r.get("sender", "") if hasattr(r, "get") else ""
            received_at = r["received_at"] if "received_at" in r.keys() else ""
            body = r["body_text"] if "body_text" in r.keys() else ""
            if not (subject or body):
                continue
            pieces.append(
                f"--- EMAIL ---\nDATE: {received_at or ''}\nFROM: {sender or ''}\n"
                f"SUBJECT: {subject or ''}\nBODY: {(body or '')[:6000]}"
            )
        return "\n".join(pieces)

    def apply_ai_result(
        self,
        ai_result: dict[str, Any],
        message: MailMessage,
        email_id: int,
        primary_email: str,
    ) -> dict[str, Any]:
        """Persist the AI's extraction for a single email into the DB.

        Returns a small summary dict used for logging/run statistics.
        """
        is_po_related = bool(ai_result.get("is_po_related"))
        po_remarks = ai_result.get("po_remarks") or ""
        po_confidence = ai_result.get("po_confidence")
        email_conclusion = ai_result.get("email_conclusion") or po_remarks

        self.db.upsert_email_details({
            "primary_email": primary_email,
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
            "is_po_related": int(is_po_related),
            "po_remarks": po_remarks,
            "po_confidence": po_confidence,
        })

        if not is_po_related:
            self.db.set_email_status(email_id, "PROCESSED")
            return {"status": "PROCESSED", "is_po_related": False, "po_numbers": []}

        pos = list(ai_result.get("pos") or [])

        # Deterministic fallback: never lose an obviously printed PO number just
        # because the AI omitted it from the structured "pos" list.
        if not pos:
            fallback_number = normalize_po_number(extract_po_number(f"{message.subject}\n{message.body_text}") or "")
            if fallback_number:
                pos = [{
                    "po_number": fallback_number, "is_existing_po": fallback_number in self.known_po_numbers(message.thread_id),
                    "company_name": "", "from_company": "", "to_company": "", "sent_by": message.sender, 
                    "po_datetime": "", "total_goods": "",
                    "distributions": [],
                }]

        received_iso = message.received_at.isoformat() if message.received_at else ""
        last_source_received_at = _received_at_utc_iso(message) or None
        po_numbers_touched: list[str] = []
        email_distributions = _email_distribution_map(message.body_text)
        mapped_po_numbers = [
            normalize_po_number(po.get("po_number") or "")
            for po in pos
            if normalize_po_number(po.get("po_number") or "") in email_distributions
        ]
        self.db.delete_source_po_distributions(email_id, mapped_po_numbers)

        for po in pos:
            po_number = normalize_po_number(po.get("po_number") or "")
            if not po_number:
                continue
            po_numbers_touched.append(po_number)

            self.db.upsert_po_details({
                "po_number": po_number,
                "company_name": po.get("company_name") or "",
                "from_company": po.get("from_company") or "",
                "to_company": po.get("to_company") or "",
                "sent_by": po.get("sent_by") or message.sender,
                "po_datetime": po.get("po_datetime") or received_iso,
                "total_goods": po.get("total_goods") or "",
                "thread_id": message.thread_id,
                "primary_email": primary_email,
                "source_email_id": email_id,
                "last_source_received_at": last_source_received_at,
            })

            distributions = list(po.get("distributions") or [])
            authoritative_distribution = email_distributions.get(po_number)
            if authoritative_distribution:
                goods = next(
                    (distribution.get("goods") for distribution in distributions if distribution.get("goods")),
                    po.get("total_goods") or "",
                )
                distributions = [{
                    **authoritative_distribution,
                    "address_date": received_iso,
                    "goods": goods,
                    "on_hold": any(bool(distribution.get("on_hold")) for distribution in distributions),
                }]

            # Handle equal distribution: if email mentions "N branches" but only provides
            # 1 address, and we know other addresses for this PO from earlier emails,
            # replicate the goods across all known addresses.
            equal_count = _detect_equal_distribution_count(f"{message.subject}\n{message.body_text}")
            if equal_count > 0 and len(distributions) == 1 and distributions[0].get("goods"):
                current_addresses = {d.get("address") for d in distributions if d.get("address")}
                known_addresses = {row["address"] for row in self.db.get_po_distributions(po_number)}
                known_addresses.update(current_addresses)
                if len(known_addresses) >= equal_count:
                    # We have at least as many addresses as mentioned, duplicate distribution
                    original_goods = distributions[0].get("goods") or ""
                    base_dist = distributions[0].copy()
                    distributions = []
                    for addr in known_addresses:
                        dist_copy = base_dist.copy()
                        dist_copy["address"] = addr
                        distributions.append(dist_copy)
                    LOGGER.info(
                        "PO %s: equal distribution detected for %s addresses (email mentioned %s MCs)",
                        po_number, len(known_addresses), equal_count,
                    )

            for dist in distributions:
                address = (dist.get("address") or "").strip()
                if not address:
                    continue
                
                # Validate state_region: if it's invalid (e.g., contains MC code, territory, etc.),
                # attempt to extract a valid state from the address. If extraction fails, leave blank.
                state_region = dist.get("state_region") or ""
                if state_region and not _is_valid_state_region(state_region):
                    extracted_state = _state_from_address(address)
                    if extracted_state:
                        LOGGER.info(
                            "PO %s: invalid state_region '%s' detected; replaced with extracted state '%s' from address",
                            po_number, state_region, extracted_state,
                        )
                        state_region = extracted_state
                    else:
                        LOGGER.warning(
                            "PO %s: invalid state_region '%s' detected; no valid state found in address '%s'; clearing field",
                            po_number, state_region, address,
                        )
                        state_region = ""
                
                self.db.upsert_po_distribution({
                    "po_number": po_number,
                    "address_date": dist.get("address_date") or received_iso,
                    "address": address,
                    "goods": dist.get("goods") or "",
                    "state_region": state_region,
                    "branch_name": dist.get("branch_name") or "",
                    "branch_code": dist.get("branch_code") or "",
                    "branch_manager_name": dist.get("branch_manager_name") or "",
                    "branch_manager_contact": dist.get("branch_manager_contact") or "",
                    "source_email_id": email_id,
                    "last_source_received_at": last_source_received_at,
                })
                if dist.get("on_hold"):
                    self.db.set_po_distribution_status(po_number, address, "ON_HOLD")
                    LOGGER.info("PO %s: address '%s' marked ON_HOLD", po_number, address)

            self.db.add_po_log({
                "po_number": po_number,
                "email_date": received_iso,
                "email_sender": message.sender,
                "email_subject": message.subject,
                "email_conclusion": email_conclusion,
                "source_email_id": email_id,
            })

            # A PO becoming known can resolve earlier "address arrived before the
            # PO" context captured in this same thread.
            self._resolve_pending_context(message.thread_id, primary_email, po_number)

        # Address/branch details shared with no PO number yet (scenario: "PO to follow later").
        if ai_result.get("has_unresolved_distribution") and ai_result.get("unresolved_distribution"):
            pending = ai_result["unresolved_distribution"]
            if (pending.get("address") or "").strip():
                self.db.add_pending_po_context({
                    "primary_email": primary_email,
                    "thread_id": message.thread_id,
                    "address_date": pending.get("address_date") or received_iso,
                    "address": pending.get("address"),
                    "goods": pending.get("goods") or "",
                    "state_region": pending.get("state_region") or "",
                    "branch_name": pending.get("branch_name") or "",
                    "branch_code": pending.get("branch_code") or "",
                    "branch_manager_name": pending.get("branch_manager_name") or "",
                    "branch_manager_contact": pending.get("branch_manager_contact") or "",
                    "company_name": "",
                    "source_email_id": email_id,
                })
                LOGGER.info(
                    "Captured address/branch context pending a PO number for thread '%s'",
                    message.thread_id,
                )

        self.db.set_email_status(email_id, "PROCESSED")
        return {"status": "PROCESSED", "is_po_related": True, "po_numbers": po_numbers_touched}

    def _resolve_pending_context(self, thread_id: str | None, primary_email: str, po_number: str) -> None:
        """Attach any earlier "address before PO" context in this thread to `po_number`."""
        pending_rows = self.db.get_pending_po_context(primary_email, thread_id)
        if not pending_rows:
            return
        resolved_ids = []
        for row in pending_rows:
            # Validate state_region: if it's invalid, attempt extraction from address
            state_region = row["state_region"] or ""
            address = row["address"] or ""
            if state_region and not _is_valid_state_region(state_region):
                extracted_state = _state_from_address(address)
                if extracted_state:
                    LOGGER.info(
                        "PO %s (pending resolve): invalid state_region '%s'; replaced with extracted state '%s'",
                        po_number, state_region, extracted_state,
                    )
                    state_region = extracted_state
                else:
                    LOGGER.warning(
                        "PO %s (pending resolve): invalid state_region '%s'; no valid state found; clearing",
                        po_number, state_region,
                    )
                    state_region = ""
            
            self.db.upsert_po_distribution({
                "po_number": po_number,
                "address_date": row["address_date"],
                "address": address,
                "goods": row["goods"],
                "state_region": state_region,
                "branch_name": row["branch_name"],
                "branch_code": row["branch_code"],
                "branch_manager_name": row["branch_manager_name"],
                "branch_manager_contact": row["branch_manager_contact"],
                "source_email_id": row["source_email_id"],
            })
            resolved_ids.append(row["id"])
        self.db.mark_pending_po_context_resolved(resolved_ids)
        LOGGER.info("Resolved %s pending address context row(s) into PO %s", len(resolved_ids), po_number)
