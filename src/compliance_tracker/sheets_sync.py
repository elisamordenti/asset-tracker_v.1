"""Syncs the Tracker into a live Google Sheet instead of a local Excel file.

This is the "living document" answer that doesn't fight file locks: a Google
Sheet is a cloud-hosted document, so there's no local file for a script and
a human to fight over the way there was with a local .xlsx (see the
PermissionError this project hit mid-build). Everything here is written and
tested against the small SheetsClient interface below, never against the
`gspread` library directly -- gspread is only imported inside
build_gspread_client(), so the core pipeline and this module's own tests
never require it to be installed. Install it with `pip install -e ".[sheets]"`
when you're ready to sync to a real sheet.
"""

from __future__ import annotations

from typing import Protocol

from compliance_tracker.config_schema import AppConfig
from compliance_tracker.excel_report import build_tracker_table
from compliance_tracker.reminder_log import ReminderSummary
from compliance_tracker.validator import AssetResult


class SheetsClient(Protocol):
    def get_all_values(self) -> list[list[str]]: ...
    def update(self, values: list[list]) -> None: ...
    def clear(self) -> None: ...


def _read_existing_notes_from_sheet(
    client: SheetsClient, notes_labels: list[str]
) -> dict[str, dict[str, str]]:
    """Same intent as excel_report._read_existing_notes, adapted to a live
    Sheet: read back {asset_id: {notes_label: value}} so hand-typed notes
    survive a sync. Any read problem is treated as "no prior notes" rather
    than raised."""
    if not notes_labels:
        return {}

    try:
        values = client.get_all_values()
    except Exception:
        return {}

    if not values:
        return {}

    headers = values[0]
    label_to_idx = {label: headers.index(label) for label in notes_labels if label in headers}
    if not label_to_idx:
        return {}

    preserved: dict[str, dict[str, str]] = {}
    for row in values[1:]:
        if not row or not row[0]:
            continue
        asset_id = row[0]
        preserved[asset_id] = {
            label: (row[idx] if idx < len(row) else "") for label, idx in label_to_idx.items()
        }
    return preserved


def sync_tracker(
    client: SheetsClient,
    config: AppConfig,
    results: list[AssetResult],
    reminder_summary: dict[str, ReminderSummary],
) -> None:
    notes_labels = [c.label for c in config.excel.notes_columns]
    existing_notes = _read_existing_notes_from_sheet(client, notes_labels)

    headers, _ordered, rows = build_tracker_table(config, results, reminder_summary, existing_notes)

    client.clear()
    client.update([headers] + rows)


def build_gspread_client(spreadsheet_id: str, worksheet_name: str, credentials_path: str) -> SheetsClient:
    """The real SheetsClient, backed by a Google service account. Only
    imports gspread/google-auth when actually called."""
    import gspread
    from google.oauth2.service_account import Credentials

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(credentials_path, scopes=scopes)
    gc = gspread.authorize(creds)
    spreadsheet = gc.open_by_key(spreadsheet_id)

    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=1000, cols=50)

    return _GspreadAdapter(worksheet)


class _GspreadAdapter:
    def __init__(self, worksheet):
        self._worksheet = worksheet

    def get_all_values(self) -> list[list[str]]:
        return self._worksheet.get_all_values()

    def update(self, values: list[list]) -> None:
        self._worksheet.update(values)

    def clear(self) -> None:
        self._worksheet.clear()
