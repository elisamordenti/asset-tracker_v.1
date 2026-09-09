"""Persistence layer for the Streamlit tracker, backed by Supabase (hosted
Postgres, free tier).

Same injectable-client pattern as sheets_sync.py and extraction.py: every
function here is written and tested against the small DBClient interface
below, never against the `supabase` package directly -- that import only
happens inside build_supabase_client(), so the test suite never needs a
real Supabase project or network access.

The `assets` table stores one row per (registry_key, asset_id) with two
separate pieces: `record` (the full merged CSV + extracted-values row --
always refreshed by sync_registry(), never hand-edited) and `notes` (free
text the user types directly into the tracker -- never touched by
sync_registry()). This mirrors the same non-destructive-merge principle used
for the Excel and Google Sheets notes columns, just backed by a real
database instead of reading a file back before overwriting it.

The `extracted_values` table is this app's equivalent of the CLI's local
extracted_values.csv overlay (extracted_values.py): every value the intake
pipeline pulls from an uploaded document is appended here (never mutating
`assets.record` directly), and sync_registry() layers the most recent value
per (asset_id, field) on top of the base CSV registry, same as the CLI. This
keeps the app fully cloud-ready -- nothing about a Supabase-backed run
depends on local disk surviving between requests.

Partitioning by `config.registry_key` rather than `config.domain` is
deliberate: two configs can set the same registry_key to become different
"lenses" (different rules, different tracked columns) over the exact same
synced pool of assets, instead of each lens getting its own empty pool just
because it has a different display name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Protocol

from compliance_tracker.archive import DocumentArchive
from compliance_tracker.config_schema import AppConfig
from compliance_tracker.extracted_values import apply_to_records, latest_from_rows
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
    def get_extraction_log(self, domain: str) -> list[dict[str, Any]]: ...  # [{source_filename, outcome, asset_id, document_type, confidence, logged_at}]
    def append_extracted_value(
        self, domain: str, asset_id: str, field: str, value: str, source_file: str, confidence: str, extracted_at: str
    ) -> None: ...
    def get_extracted_values(self, domain: str) -> list[dict[str, Any]]: ...  # [{asset_id, field, value, extracted_at}]
    def save_draft(self, domain: str, asset_id: str, to_name: str, to_email: str, subject: str, body: str) -> None: ...
    def get_drafts(self, domain: str) -> list[dict[str, Any]]: ...  # [{asset_id, to_name, to_email, subject, body, sent_at}]
    def mark_draft_sent(self, domain: str, asset_id: str) -> None: ...


def sync_registry(client: DBClient, config: AppConfig) -> int:
    """Load the base CSV registry, merge in this registry's extracted_values
    overlay (from the intake/upload pipeline), and upsert every asset's
    `record`. Never touches `notes`."""
    base_records = build_loader(config.source).load()
    latest = latest_from_rows(client.get_extracted_values(config.registry_key))
    records = apply_to_records(base_records, latest, config.source.id_field)

    for record in records:
        client.upsert_record(config.registry_key, record[config.source.id_field], record)
    return len(records)


def load_results_and_notes(
    client: DBClient, config: AppConfig, archive: DocumentArchive | None = None
) -> tuple[list[AssetResult], dict[str, dict[str, str]]]:
    rows = client.get_assets(config.registry_key)
    records = [row["record"] for row in rows]
    notes_by_asset = {row["asset_id"]: row.get("notes") or {} for row in rows}
    results = validate_assets(config, records=records, archive=archive)
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


def load_needs_review(client: DBClient, domain: str) -> list[dict[str, Any]]:
    """Every logged extraction attempt that didn't result in a confident,
    auto-filed document -- i.e. still sitting under the archive's
    `_pending_review` prefix, waiting for a human. Most recent first."""
    return [entry for entry in client.get_extraction_log(domain) if entry["outcome"] != "filed"]


def save_drafts(client: DBClient, domain: str, drafts) -> None:
    """Persist drafted reminder emails so the review screen survives a
    restart -- one row per asset, overwriting any earlier unsent draft for
    that same asset (a fresh reminder cycle replaces the last one)."""
    for draft in drafts:
        client.save_draft(domain, draft.asset_id, draft.to_name, draft.to_email, draft.subject, draft.body)


def load_pending_drafts(client: DBClient, domain: str) -> list[dict[str, Any]]:
    """Drafts that exist but haven't been marked sent yet -- what the
    Reminders review screen should show, loaded fresh on every page render
    instead of relying on in-memory session state."""
    return [d for d in client.get_drafts(domain) if not d.get("sent_at")]


def mark_draft_sent(client: DBClient, domain: str, asset_id: str) -> None:
    client.mark_draft_sent(domain, asset_id)


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

    def get_extraction_log(self, domain: str) -> list[dict[str, Any]]:
        resp = (
            self._raw.table("extraction_log")
            .select("source_filename, outcome, asset_id, document_type, confidence, logged_at")
            .eq("domain", domain)
            .order("logged_at", desc=True)
            .limit(50)
            .execute()
        )
        return resp.data

    def append_extracted_value(
        self, domain: str, asset_id: str, field: str, value: str, source_file: str, confidence: str, extracted_at: str
    ) -> None:
        self._raw.table("extracted_values").insert({
            "domain": domain,
            "asset_id": asset_id,
            "field": field,
            "value": value,
            "source_file": source_file,
            "confidence": confidence,
            "extracted_at": extracted_at,
        }).execute()

    def get_extracted_values(self, domain: str) -> list[dict[str, Any]]:
        resp = (
            self._raw.table("extracted_values")
            .select("asset_id, field, value, extracted_at")
            .eq("domain", domain)
            .execute()
        )
        return resp.data

    def save_draft(self, domain: str, asset_id: str, to_name: str, to_email: str, subject: str, body: str) -> None:
        # A fresh draft always overwrites any earlier one for the same asset
        # and resets sent_at -- re-drafting starts a new, unsent review cycle.
        self._raw.table("email_drafts").upsert(
            {
                "domain": domain, "asset_id": asset_id, "to_name": to_name, "to_email": to_email,
                "subject": subject, "body": body, "sent_at": None,
            },
            on_conflict="domain,asset_id",
        ).execute()

    def get_drafts(self, domain: str) -> list[dict[str, Any]]:
        resp = (
            self._raw.table("email_drafts")
            .select("asset_id, to_name, to_email, subject, body, sent_at")
            .eq("domain", domain)
            .execute()
        )
        return resp.data

    def mark_draft_sent(self, domain: str, asset_id: str) -> None:
        sent_at = datetime.now(timezone.utc).isoformat()
        self._raw.table("email_drafts").update({"sent_at": sent_at}).eq("domain", domain).eq(
            "asset_id", asset_id
        ).execute()
