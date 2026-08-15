"""Defensive Gemini REST client for PO email/thread extraction.

Behaviour notes:
1. A configured model can return HTTP 404 because the model is unavailable to
   the API key/project. The client discovers available generateContent models
   and falls back to a compatible Flash model.
2. Gemini failures are classified and logged clearly, with limited retries for
   transient errors (429/5xx/network) and no retries for permanent ones (400).

The API key remains outside source code and is read from config.json.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import requests

from app.core.config import AIConfig

LOGGER = logging.getLogger(__name__)

_GOODS_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "address": {"type": "string"},
        "address_date": {"type": "string"},
        "goods": {"type": "string"},
        "state_region": {"type": "string"},
        "branch_name": {"type": "string"},
        "branch_code": {"type": "string"},
        "branch_manager_name": {"type": "string"},
        "branch_manager_contact": {"type": "string"},
        "on_hold": {"type": "boolean"},
    },
    "required": [
        "address", "address_date", "goods", "state_region", "branch_name",
        "branch_code", "branch_manager_name", "branch_manager_contact", "on_hold",
    ],
}

PO_SCHEMA = {
    "type": "object",
    "properties": {
        "is_po_related": {"type": "boolean"},
        "po_remarks": {"type": "string"},
        "po_confidence": {"type": "integer"},
        "email_conclusion": {"type": "string"},
        "pos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "po_number": {"type": "string"},
                    "is_existing_po": {"type": "boolean"},
                    "company_name": {"type": "string"},
                    "from_company": {"type": "string"},
                    "to_company": {"type": "string"},
                    "sent_by": {"type": "string"},
                    "po_datetime": {"type": "string"},
                    "total_goods": {"type": "string"},
                    "distributions": {"type": "array", "items": _GOODS_ITEM_SCHEMA},
                },
                "required": [
                    "po_number", "is_existing_po", "company_name", "sent_by",
                    "po_datetime", "total_goods", "distributions",
                ],
            },
        },
        "unresolved_distribution": _GOODS_ITEM_SCHEMA,
        "has_unresolved_distribution": {"type": "boolean"},
    },
    "required": [
        "is_po_related", "po_remarks", "po_confidence", "email_conclusion",
        "pos", "unresolved_distribution", "has_unresolved_distribution",
    ],
}

SYSTEM_INSTRUCTION = """
You are a document extraction component inside a business Purchase-Order (PO)
email analyzer. Return ONLY valid JSON matching the supplied schema. Never
invent data - use an empty string ("") or false when a field is not explicitly
supported by the email, attachment, or prior thread context supplied to you.

TASK 1 - Classify the CURRENT email:
- is_po_related: true if this email is related to a purchase order in any way
  (a PO document, PO number reference, delivery address/branch/contact details
  for a PO, a hold/cancellation/update notice, or quantity/goods details tied
  to a PO), even if the PO document itself is not attached to this email.
- po_confidence: your confidence that this email is PO-related, as an INTEGER
  from 1 (not at all confident) to 10 (fully confident).
- po_remarks: a short, plain-English note explaining WHY you classified it
  that way.
- email_conclusion: a short, crisp, plain-English summary of what this
  SPECIFIC email communicated, for a per-PO communication log. Prefer these
  exact phrasing patterns (fill in the placeholders, keep it to one sentence):
    * New PO: "New <PO_NUMBER> PO is shared by <sender_email> on <date>."
    * Revision to an existing PO: "<sender_name_or_email> shared the revision
      of <PO_NUMBER> PO on <date>."
    * Address/branch details: "<sender_name_or_email> shared delivery address
      for <PO_NUMBER> PO on <date>."
    * Hold notice: "<sender_name_or_email> put <address_or_branch> on hold for
      <PO_NUMBER> PO on <date>."
    * Cancellation: "<sender_name_or_email> cancelled <PO_NUMBER> PO on <date>."
  Use the CURRENT email's sender and its own received date/time for <date>.
  If the email is not PO-related, leave email_conclusion blank.

TASK 2 - Extract or link PO information:
You will be given the full email thread history (oldest first) and a list of
PO numbers already known in this thread. Use them to correctly link new
information back to the right PO, even when it arrives in a separate email:

1. A PO may be a PDF/image attachment - read it fully.
2. An email may share only an address/branch, with a note that the PO will
   follow later. In that case leave po_number empty for that item and still
   fill "unresolved_distribution" with everything you know (set
   has_unresolved_distribution=true), so it can be linked once the PO number
   is known.
3. A PO document usually states the total quantity of goods to be delivered
   to a specific branch - capture this under that PO's distribution.
4. Multiple POs can be attached in a single email with no address/branch
   details at all - return one entry per PO number, each with an empty
   distributions list.
5. The PO may only list total goods, with delivery addresses shared later in
   separate emails in the SAME thread (sometimes only some addresses at a
   time). When you see later address/branch emails, set is_existing_po=true
   and po_number to the matching PO number already known in this thread.
6. Later emails in the same thread may specify how much quantity goes to
   which branch/location - attach these as distributions of the matching
   existing PO number.
7. A single email can contain both the PO attachment AND full address/contact
   details together, or multiple PO attachments each with address/contact
   details in the body - extract every PO and its distributions from that
   one email.
8. A location can be put on hold in a LATER email in the same thread. When
   this happens, return that PO's number (is_existing_po=true) with a
   distribution entry for that same address with on_hold=true. Do not
   fabricate other fields for a hold-only update - leave them empty except
   the address and on_hold.
9. Branch address/contact details for a PO can be corrected/updated later in
   the same thread. Return the PO number (is_existing_po=true) with a
   distribution entry for that address containing the CORRECTED fields.

Formatting rules:
- "total_goods" and "goods" (per distribution) must be formatted as:
  "Goods1 Name (quantity), Goods2 Name (quantity)".
- A PO number found in an attachment, the email thread, or the email body all
  count as valid PO number sources.
- If a field is uncertain, leave it blank rather than guessing.
- Only ever put ONE distribution per address in the array for a given PO; if
  the same address appears multiple times in this single email, merge it into
  one entry.
- "state_region" MUST be extracted ONLY from the actual geographic/administrative
  state or region name found within the delivery address. CRITICAL RULES:
  * ONLY extract genuine Indian state/region names (e.g., "Delhi", "Maharashtra",
    "Gujarat", "Karnataka", "Tamil Nadu", "Uttar Pradesh", etc.)
  * DO NOT extract or include: branch names, MC (Management Center) names,
    territory codes, location codes (e.g., "MC-01", "Territory-North", "Branch-A"),
    or abbreviations like "TN", "MH", "UP"
  * If the address mentions both a state AND a branch/MC name, ONLY extract
    the state name and leave branch/MC/territory information for the branch_name
    field instead
  * If the address contains no recognizable Indian state name, leave state_region
    blank (do NOT fill with branch name, territory, or location code as a fallback)
  * Examples of CORRECT extraction:
    - Address: "Store #12, Delhi" → state_region: "Delhi"
    - Address: "Placed with MAVA, Mumbai" → state_region: "Mumbai"
    - Address: "Territory-South, Bangalore, Karnataka" → state_region: "Karnataka"
    - Address: "MC-05, Pune" → state_region: "Pune"
  * Examples of INCORRECT extraction (DO NOT DO THIS):
    - Address: "MC-05, Mumbai" → DO NOT put "MC-05" in state_region
    - Address: "Territory-North, Delhi" → DO NOT put "Territory-North" in state_region
    - Address: "Branch Alpha, Chennai" → DO NOT put "Branch Alpha" in state_region
- When a PO states it is for multiple branches (e.g., "This PO covers 5 MCs"),
  but provides only partial address information in that email, still return
  only the address(es) explicitly mentioned - do not fabricate additional
  branches. The equal distribution logic will be handled at import time if
  thread context includes other known addresses for the same PO.
- "from_company": Extract the company name FROM WHICH the PO is received/sent.
  This is typically found in:
    * The PO document header/title (e.g., "PO from ABC Corporation")
    * The company letterhead/branding on the PO attachment
    * Sender's company affiliation if they are representing a company
  If you see text like "from ABC Corporation" or "PO by XYZ Ltd", extract that
  as from_company. Leave blank if no explicit source company is mentioned.
- "to_company": Extract the company name TO WHICH the PO is directed/placed.
  This is typically found in:
    * Recipient or delivery-to company name
    * Text like "placed with [COMPANY_NAME]" or "for [COMPANY_NAME]"
    * The company that will be working on/fulfilling the PO
  If you see wording like "placed with MAVA INTERNATIONAL" or "PO for XYZ Ltd",
  extract that as to_company. Leave blank if no explicit recipient company is
  mentioned.
- company_name: For backward compatibility, populate this with whichever of
  from_company or to_company is more prominent, or from_company if both are
  equally prominent.
""".strip()


class GeminiError(RuntimeError):
    """Raised when Gemini cannot safely produce a structured result."""


class GeminiClient:
    def __init__(self, config: AIConfig):
        self.config = config
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self.model = config.model.strip() or "gemini-3.5-flash-lite"
        self.url = self._model_url(self.model)
        self._model_checked = False

    def _model_url(self, model: str) -> str:
        model = model.removeprefix("models/")
        return f"{self.base_url}/models/{model}:generateContent"

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self.config.api_key,
        }

    def _discover_model(self) -> str:
        """Find a model available to this API key that supports generateContent."""
        if self._model_checked:
            return self.model

        self._model_checked = True

        try:
            response = requests.get(
                f"{self.base_url}/models",
                headers={"x-goog-api-key": self.config.api_key},
                params={"pageSize": 1000},
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            models = response.json().get("models", [])

            supported: list[str] = []
            for item in models:
                name = str(item.get("name", "")).removeprefix("models/")
                methods = item.get("supportedGenerationMethods") or []
                if name and "generateContent" in methods:
                    supported.append(name)

            configured = self.model
            if configured in supported:
                LOGGER.info("Gemini model '%s' is available", configured)
                return configured

            preferred_fragments = (
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-3-flash",
                "gemini-3.1-flash",
            )

            for fragment in preferred_fragments:
                for candidate in supported:
                    if fragment in candidate and "image" not in candidate:
                        self.model = candidate
                        self.url = self._model_url(candidate)
                        LOGGER.warning(
                            "Configured Gemini model '%s' is unavailable; using available model '%s'",
                            configured, candidate,
                        )
                        return candidate

            if supported:
                self.model = supported[0]
                self.url = self._model_url(supported[0])
                LOGGER.warning(
                    "Configured Gemini model '%s' is unavailable; using first available model '%s'",
                    configured, supported[0],
                )
                return supported[0]

            raise GeminiError(
                "Gemini API returned no models supporting generateContent. "
                "Check the API key/project and Gemini API access."
            )

        except requests.RequestException as exc:
            raise GeminiError(f"Could not query Gemini model availability: {exc}") from exc

    def extract(
        self,
        *,
        subject: str,
        body: str,
        sender: str = "",
        received_at: str = "",
        attachment_bytes: bytes | None = None,
        attachment_mime: str | None = None,
        thread_context: str = "",
        known_po_numbers: list[str] | None = None,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            raise GeminiError("Gemini is disabled in configuration")
        if not self.config.api_key:
            raise GeminiError("Gemini API key is missing")
        if attachment_bytes and len(attachment_bytes) > self.config.max_pdf_bytes:
            raise GeminiError(
                f"Attachment exceeds configured AI size limit ({self.config.max_pdf_bytes} bytes)"
            )

        self._discover_model()

        known_po_text = (
            ("KNOWN PO NUMBERS ALREADY SEEN IN THIS THREAD: " + ", ".join(known_po_numbers))
            if known_po_numbers else
            "KNOWN PO NUMBERS ALREADY SEEN IN THIS THREAD: (none yet)"
        )

        parts: list[dict[str, Any]] = [{
            "text": (
                f"{SYSTEM_INSTRUCTION}\n\n"
                f"{known_po_text}\n\n"
                f"CURRENT EMAIL FROM: {sender}\n"
                f"CURRENT EMAIL DATE: {received_at}\n"
                f"CURRENT EMAIL SUBJECT:\n{subject}\n\n"
                f"CURRENT EMAIL BODY:\n{body[:50000]}\n\n"
                "FULL THREAD HISTORY (oldest first, for context only - do NOT "
                "re-report conclusions about older emails, only the CURRENT one):\n"
                f"{thread_context[:40000]}"
            )
        }]

        if attachment_bytes and attachment_mime:
            parts.append({
                "inline_data": {
                    "mime_type": attachment_mime,
                    "data": base64.b64encode(attachment_bytes).decode("ascii"),
                }
            })

        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": PO_SCHEMA,
            },
        }

        last_error: Exception | None = None

        for attempt in range(1, self.config.max_retries + 1):
            try:
                response = requests.post(
                    self.url, headers=self._headers(), json=payload, timeout=self.config.timeout_seconds,
                )

                if response.status_code == 404:
                    configured_model = self.model
                    self._model_checked = False
                    self.model = self.config.model.strip() or configured_model
                    try:
                        self._discover_model()
                    except GeminiError:
                        pass
                    if self.url != self._model_url(configured_model):
                        LOGGER.warning("Gemini model returned 404; retrying with discovered model '%s'", self.model)
                        response = requests.post(
                            self.url, headers=self._headers(), json=payload, timeout=self.config.timeout_seconds,
                        )

                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(
                        f"Retryable Gemini HTTP {response.status_code}: {response.text[:1000]}"
                    )

                if response.status_code == 400:
                    raise GeminiError(f"Gemini rejected the request (400): {response.text[:2000]}")

                response.raise_for_status()

                data = response.json()
                text = self._response_text(data)
                result = json.loads(text)
                self._validate_result(result)
                return result

            except GeminiError as exc:
                last_error = exc
                LOGGER.error(
                    "Gemini extraction failed (attempt %s/%s): %s", attempt, self.config.max_retries, exc,
                )
                break

            except requests.RequestException as exc:
                last_error = exc
                LOGGER.warning(
                    "Gemini attempt %s/%s failed: %s", attempt, self.config.max_retries, exc,
                )
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_backoff_seconds * (2 ** (attempt - 1)))

            except (ValueError, KeyError) as exc:
                last_error = exc
                LOGGER.warning(
                    "Gemini response parsing attempt %s/%s failed: %s", attempt, self.config.max_retries, exc,
                )
                if attempt < self.config.max_retries:
                    time.sleep(self.config.retry_backoff_seconds * (2 ** (attempt - 1)))

        raise GeminiError(f"Gemini request failed after {self.config.max_retries} attempts: {last_error}")

    @staticmethod
    def _response_text(data: dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            raise GeminiError(f"Gemini returned no candidates: {json.dumps(data)[:2000]}")

        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(
            str(p.get("text", "")) for p in parts if isinstance(p, dict) and "text" in p
        ).strip()

        if not text:
            raise GeminiError(f"Gemini returned an empty response: {json.dumps(data)[:2000]}")

        return text

    @staticmethod
    def _validate_result(result: dict[str, Any]) -> None:
        if not isinstance(result, dict):
            raise GeminiError("Gemini response was not a JSON object")
        if "is_po_related" not in result:
            raise GeminiError("Gemini response is missing required field 'is_po_related'")
        confidence = result.get("po_confidence")
        if confidence is not None:
            try:
                result["po_confidence"] = max(1, min(10, int(confidence)))
            except (TypeError, ValueError):
                result["po_confidence"] = 1
