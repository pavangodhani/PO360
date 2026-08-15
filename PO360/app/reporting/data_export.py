"""Raw-table Excel export: PO Details, PO Distribution, PO Logs, Email Details.

This is a separate, simpler export from `app/reporting/excel.py` (which is a
deferred "master business view" report). This one exports the four business
tables as-is, filtered to a From/To date-time window, for one workbook with
exactly 4 sheets in this exact order:

    1. PO Details
    2. PO Distribution
    3. PO Logs
    4. Email Details

Assumption - which date field each sheet is filtered by (documented since the
requirement only gave "e.g. email received date" as an example):
    * Email Details    -> received_at        (email received date/time)
    * PO Details       -> updated_at         (last time this PO record changed)
    * PO Distribution  -> updated_at         (last time this delivery location changed)
    * PO Logs          -> email_date         (the date of the email that produced this log line)
PO Details/PO Distribution use `updated_at` rather than the original
po_datetime/address_date so a PO/address that had activity in the window is
included even if it was first created earlier - this matches the periodic
"what changed recently" reporting use case implied by the request.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

LOGGER = logging.getLogger(__name__)

# Display format for date/time values written into the workbook and used in
# the output file name - a local, human-readable long form (e.g. "15 Aug 2026
# 18:30:00") rather than raw ISO8601.
DISPLAY_DATETIME_FORMAT = "%d %b %Y %H:%M:%S"
FILENAME_DATETIME_FORMAT = "%d-%b-%Y_%H-%M-%S"
DATE_ONLY_FORMAT = "%d %b %Y"

# Columns to export for each sheet. Technical columns (thread_id, created_at,
# updated_at, source_email_id, status) are excluded per client feedback.
SHEETS: list[tuple[str, str, list[str]]] = [
    ("PO Details", "export_po_details", [
        "id", "po_number", "from_company", "to_company", "sent_by", "po_date", "total_goods",
        "primary_email",
    ]),
    ("PO Distribution", "export_po_distribution", [
        "id", "po_number", "address_date", "address", "goods", "state_region",
        "branch_name", "branch_code", "branch_manager_name", "branch_manager_contact",
    ]),
    ("PO Logs", "export_po_logs", [
        "id", "po_number", "email_date", "email_sender", "email_subject",
        "email_conclusion",
    ]),
    ("Email Details", "export_email_details", [
        "id", "primary_email", "message_id", "subject", "sender_email",
        "to_recipients", "cc_recipients", "received_at", "attachment_count",
        "attachment_names", "is_po_related", "po_remarks", "po_confidence",
        "processing_error",
    ]),
]

# Date/time columns that should be formatted as date-only (no time component)
DATE_ONLY_COLUMNS = {
    "po_date", "address_date", "email_date", "received_at",
}


def _cell_value(row: Any, column: str) -> Any:
    """Extract and format a cell value. Handle date-only formatting and bool conversion."""
    value = row[column]
    if column in {"is_po_related"} and value is not None:
        return "Yes" if value else "No"
    # Format date/time columns as date-only (remove time component)
    if column in DATE_ONLY_COLUMNS and value is not None:
        try:
            if isinstance(value, str) and len(value) > 10:  # ISO format with time
                dt = datetime.fromisoformat(value)
                return dt.strftime(DATE_ONLY_FORMAT)
        except (ValueError, AttributeError):
            pass
    return value


def export_data(db, from_dt: datetime, to_dt: datetime, output_dir: str | Path) -> Path:
    """Export the 4 business tables to a single workbook, filtered to [from_dt, to_dt].

    Raises RuntimeError with a clear message on any failure (e.g. output path
    not writable) so the CLI can log and exit non-zero without a raw traceback.
    """
    output_dir = Path(output_dir)
    from_iso = from_dt.isoformat()
    to_iso = to_dt.isoformat()

    try:
        output_dir.mkdir(parents=True, exist_ok=True)

        wb = Workbook()
        wb.remove(wb.active)

        sheet_counts: dict[str, int] = {}
        for sheet_name, query_method, columns in SHEETS:
            ws = wb.create_sheet(sheet_name)
            ws.append(columns)
            rows = getattr(db, query_method)(from_iso, to_iso)
            for row in rows:
                ws.append([_cell_value(row, column) for column in columns])
            _style_sheet(ws)
            sheet_counts[sheet_name] = len(rows)

        filename = (
            f"PO_Export_{from_dt.strftime(FILENAME_DATETIME_FORMAT)}"
            f"_to_{to_dt.strftime(FILENAME_DATETIME_FORMAT)}.xlsx"
        )
        output_path = output_dir / filename
        wb.save(output_path)
    except PermissionError as exc:
        LOGGER.error("Cannot write export workbook in '%s'. It may be open in Excel.", output_dir, exc_info=True)
        raise RuntimeError(
            f"Cannot write the export file in {output_dir}. Close it in Excel and run again."
        ) from exc
    except OSError as exc:
        LOGGER.error("Failed writing export workbook in '%s': %s", output_dir, exc, exc_info=True)
        raise RuntimeError(f"Failed writing the export file in {output_dir}: {exc}") from exc

    LOGGER.info(
        "Excel export generated: %s (from=%s to=%s, %s)",
        output_path,
        from_dt.strftime(DISPLAY_DATETIME_FORMAT),
        to_dt.strftime(DISPLAY_DATETIME_FORMAT),
        ", ".join(f"{name}={count}" for name, count in sheet_counts.items()),
    )
    return output_path


def _style_sheet(ws) -> None:
    ws.freeze_panes = "A2"
    if ws.max_row >= 1:
        ws.auto_filter.ref = ws.dimensions

    # Columns that need minimum width for readability (especially on Windows)
    MIN_COL_WIDTHS = {
        "email_conclusion": 35,
        "remarks": 30,
        "address": 30,
        "email_subject": 30,
    }

    for cell in ws[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.fill = PatternFill(fill_type="solid", fgColor="D9EAF7")

    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    for col_cells in ws.columns:
        header = col_cells[0].value or ""
        min_width = MIN_COL_WIDTHS.get(str(header), 12)
        max_len = 0
        for cell in col_cells:
            value = "" if cell.value is None else str(cell.value)
            max_len = max(max_len, min(len(value), 60))
        width = max(min_width, min(max_len + 2, 60))
        ws.column_dimensions[get_column_letter(col_cells[0].column)].width = width

    ws.row_dimensions[1].height = 32
