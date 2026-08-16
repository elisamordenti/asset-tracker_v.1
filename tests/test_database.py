"""No `supabase` import anywhere in this file -- every function is tested
purely against the DBClient interface, via this in-memory fake."""

from datetime import date

from compliance_tracker.config_schema import (
    AppConfig,
    ColumnConfig,
    ContactConfig,
    EmailConfig,
    ExcelConfig,
    RuleConfig,
    SourceConfig,
)
from compliance_tracker.database import (
    append_reminders,
    load_reminder_summary,
    load_results_and_notes,
    record_extraction_outcome,
    save_note,
    sync_registry,
)


class FakeDBClient:
    def __init__(self):
        self.assets = {}  # (domain, asset_id) -> {"record": ..., "notes": ...}
        self.reminders = []
        self.extraction_logs = []

    def upsert_record(self, domain, asset_id, record):
        key = (domain, asset_id)
        existing = self.assets.get(key, {"notes": {}})
        self.assets[key] = {"record": record, "notes": existing.get("notes", {})}

    def get_assets(self, domain):
        return [
            {"asset_id": asset_id, "record": v["record"], "notes": v["notes"]}
            for (d, asset_id), v in self.assets.items()
            if d == domain
        ]

    def set_notes(self, domain, asset_id, notes):
        key = (domain, asset_id)
        self.assets[key]["notes"] = notes

    def append_reminder(self, domain, asset_id, sent_date, reminder_number):
        self.reminders.append(
            {"domain": domain, "asset_id": asset_id, "date": sent_date, "reminder_number": reminder_number}
        )

    def get_reminders(self, domain):
        return [r for r in self.reminders if r["domain"] == domain]

    def append_extraction_log(self, domain, entry):
        self.extraction_logs.append({"domain": domain, **entry})


def build_config(tmp_path, csv_content):
    csv_path = tmp_path / "assets.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    return AppConfig(
        domain="Test Domain",
        source=SourceConfig(type="csv", path=str(csv_path), id_field="asset_id"),
        contact=ContactConfig(name_field="contact_name", email_field="contact_email"),
        rules=[
            RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                       message="low margin", label="Margin", params={"min": 5}),
        ],
        excel=ExcelConfig(info_columns=[ColumnConfig(field="asset_id", label="ID")]),
        email=EmailConfig(subject_template="s", body_template="b"),
    )


def test_sync_registry_upserts_every_record(tmp_path):
    config = build_config(tmp_path, "asset_id,margin_pct,contact_name,contact_email\nAST-1,10,Dana,dana@example.com\n")
    client = FakeDBClient()

    count = sync_registry(client, config, tmp_path / "output")

    assert count == 1
    assert client.get_assets("Test Domain")[0]["record"]["margin_pct"] == "10"


def test_sync_registry_never_touches_existing_notes(tmp_path):
    config = build_config(tmp_path, "asset_id,margin_pct,contact_name,contact_email\nAST-1,10,Dana,dana@example.com\n")
    client = FakeDBClient()
    sync_registry(client, config, tmp_path / "output")
    save_note(client, "Test Domain", "AST-1", {"Notes": "already following up"})

    sync_registry(client, config, tmp_path / "output")  # re-sync

    assets = client.get_assets("Test Domain")
    assert assets[0]["notes"] == {"Notes": "already following up"}


def test_load_results_and_notes_runs_validator_against_db_records(tmp_path):
    config = build_config(tmp_path, "asset_id,margin_pct,contact_name,contact_email\nAST-1,2,Dana,dana@example.com\n")
    client = FakeDBClient()
    sync_registry(client, config, tmp_path / "output")

    results, notes = load_results_and_notes(client, config)

    assert len(results) == 1
    assert results[0].violations  # margin 2 < 5
    assert notes == {"AST-1": {}}


def test_append_reminders_increments_across_calls(tmp_path):
    client = FakeDBClient()
    append_reminders(client, "Test Domain", ["AST-1"], run_date=date(2026, 8, 1))
    append_reminders(client, "Test Domain", ["AST-1"], run_date=date(2026, 8, 8))

    summary = load_reminder_summary(client, "Test Domain")
    assert summary["AST-1"].count == 2
    assert summary["AST-1"].last_sent == "2026-08-08"


def test_record_extraction_outcome_logs_needs_review(tmp_path):
    client = FakeDBClient()

    class FakeOutcome:
        source_filename = "scan.pdf"
        outcome = "needs_review"
        asset_id = None
        document_type_rule_id = None
        confidence = "low"

    record_extraction_outcome(client, "Test Domain", FakeOutcome())

    assert client.extraction_logs == [
        {"domain": "Test Domain", "source_filename": "scan.pdf", "outcome": "needs_review",
         "asset_id": "", "document_type": "", "confidence": "low"}
    ]
