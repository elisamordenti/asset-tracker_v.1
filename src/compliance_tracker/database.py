"""Persistence layer for the Streamlit tracker, backed by Supabase (hosted
Postgres, free tier).

Same injectable-client pattern as sheets_sync.py and extraction.py: every
function here is written and tested against the small DBClient interface
below, never against the `supabase` package directly -- that import only
happens inside build_supabase_client(), so the test suite never needs a
real Supabase project or network access.

The `assets` table stores one row per (domain, asset_id) with two separate
pieces: `record` (the full merged CSV + extracted-values row -- always
refreshed by sync_registry(), never hand-edited) and `notes` (free text the
user types directly into the tracker -- never touched by sync_registry()).
This mirrors the same non-destructive-merge principle used for the Excel and
Google Sheets notes columns, just backed by a real database instead of
reading a file back before overwriting it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from compliance_tracker.config_schema import AppConfig
from compliance_tracker.extracted_values import apply_to_records
from compliance_tracker.loaders import build_loader
from compliance_tracker.reminder_log import ReminderSummary, summarize
from compliance_tracker.validator import AssetResult, validate_assets


class DBClient(Protocol):
    def upsert_record(self, domain: str, asset_id: str, record: dict[str, Any]) -> None: ...
    def get_assets(self, domain: str) -> list[dict[str, Any]]: ...  # [{asset_id, record, notes}]
    def set_notes(self, domain: str, asset_id: str, notes: dict[str, str]) -> None: ...
    def append_reminder(self, domain: str, asset_id: str, sent_date: str, reminder_number: int) -> None: ...
    def get_reminders(self, domain: str) -> list[dict[str, Any]]: ...  # [{asset_id, date, reminder_number}]
    def append_extraction_log(self, domain: str, entry: dict[str, str]) -> None: ...


def sync_registry(client: DBClient, config: AppConfig, base_output_dir: str | Path) -> int:
    """Load the base CSV registry, merge in extracted_values.csv if one
    exists (unchanged from the local/Sheets paths), and upsert every asset's
    `record`. Never touches `notes`."""
    base_records = build_loader(config.source).load()

    extracted_values_path = Path(base_output_dir) / "extracted_values.csv"
    records = (
        apply_to_records(base_records, extracted_values_path, config.source.id_field)
        if extracted_values_path.exists()
        else base_records
    )

    for record in records:
        client.upsert_record(config.domain, record[config.source.id_field], record)
    return len(records)


def load_results_and_notes(
    client: DBClient, config: AppConfig
) -> tuple[list[AssetResult], dict[str, dict[str, str]]]:
    rows = client.get_assets(config.domain)
    records = [row["record"] for row in rows]
    notes_by_asset = {row["asset_id"]: row.get("notes") or {} for row in rows}
    results = validate_assets(config, records=records)
    return results, notes_by_asset


def save_note(client: DBClient, domain: str, asset_id: str, notes: dict[str, str]) -> None:
    client.set_notes(domain, asset_id, notes)


def load_reminder_summary(client: DBClient, domain: str) -> dict[str, ReminderSummary]:
    return summarize(client.get_reminders(domain))


def append_reminders(
    client: DBClient, domain: str, asset_ids: list[str], run_date: date | None = None
) -> None:
    run_date = run_date or date.today()
    current = load_reminder_summary(client, domain)
    for asset_id in asset_ids:
        next_number = current.get(asset_id, ReminderSummary(None, 0)).count + 1
        client.append_reminder(domain, asset_id, run_date.isoformat(), next_number)
        current[asset_id] = ReminderSummary(last_sent=run_date.isoformat(), count=next_number)


def record_extraction_outcome(client: DBClient, domain: str, outcome) -> None:
    client.append_extraction_log(
        domain,
        {
            "source_filename": outcome.source_filename,
            "outcome": outcome.outcome,
            "asset_id": outcome.asset_id or "",
            "document_type": outcome.document_type_rule_id or "",
            "confidence": outcome.confidence,
        },
    )


def build_supabase_client(url: str, key: str) -> DBClient:
    """The real DBClient, backed by a Supabase project. Only imports
    `supabase` when actually called."""
    from supabase import create_client

    return _SupabaseDBClient(create_client(url, key))


@dataclass
class _SupabaseDBClient:
    _raw: Any

    def upsert_record(self, domain: str, asset_id: str, record: dict[str, Any]) -> None:
        # `notes` is deliberately excluded from this payload: PostgREST's
        # merge-duplicates upsert only sets columns present in the request,
        # so an existing row's `notes` is left untouched on conflict, and a
        # brand-new row gets the column default ('{}'::jsonb).
        self._raw.table("assets").upsert(
            {"domain": domain, "asset_id": asset_id, "record": record},
            on_conflict="domain,asset_id",
        ).execute()

    def get_assets(self, domain: str) -> list[dict[str, Any]]:
        resp = self._raw.table("assets").select("asset_id, record, notes").eq("domain", domain).execute()
        return resp.data

    def set_notes(self, domain: str, asset_id: str, notes: dict[str, str]) -> None:
        self._raw.table("assets").update({"notes": notes}).eq("domain", domain).eq("asset_id", asset_id).execute()

    def append_reminder(self, domain: str, asset_id: str, sent_date: str, reminder_number: int) -> None:
        self._raw.table("reminder_log").insert(
            {"domain": domain, "asset_id": asset_id, "sent_date": sent_date, "reminder_number": reminder_number}
        ).execute()

    def get_reminders(self, domain: str) -> list[dict[str, Any]]:
        resp = (
            self._raw.table("reminder_log")
            .select("asset_id, sent_date, reminder_number")
            .eq("domain", domain)
            .execute()
        )
        return [
            {"asset_id": r["asset_id"], "date": r["sent_date"], "reminder_number": r["reminder_number"]}
            for r in resp.data
        ]

    def append_extraction_log(self, domain: str, entry: dict[str, str]) -> None:
        self._raw.table("extraction_log").insert({"domain": domain, **entry}).execute()
