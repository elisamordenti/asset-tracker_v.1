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
    load_needs_review,
    load_pending_drafts,
    load_reminder_summary,
    load_results_and_notes,
    mark_draft_sent,
    record_extraction_outcome,
    save_drafts,
    save_note,
    sync_registry,
)


class FakeDBClient:
    def __init__(self):
        self.assets = {}  # (domain, asset_id) -> {"record": ..., "notes": ...}
        self.reminders = []
        self.extraction_logs = []
        self.extracted_values = []
        self.drafts = {}  # (domain, asset_id) -> {...}

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

    def get_extraction_log(self, domain):
        return [
            {k: v for k, v in entry.items() if k != "domain"}
            for entry in reversed(self.extraction_logs)
            if entry["domain"] == domain
        ]

    def append_extracted_value(self, domain, asset_id, field, value, source_file, confidence, extracted_at):
        self.extracted_values.append({
            "domain": domain, "asset_id": asset_id, "field": field, "value": value,
            "source_file": source_file, "confidence": confidence, "extracted_at": extracted_at,
        })

    def get_extracted_values(self, domain):
        return [r for r in self.extracted_values if r["domain"] == domain]

    def save_draft(self, domain, asset_id, to_name, to_email, subject, body):
        self.drafts[(domain, asset_id)] = {
            "asset_id": asset_id, "to_name": to_name, "to_email": to_email,
            "subject": subject, "body": body, "sent_at": None,
        }

    def get_drafts(self, domain):
        return [v for (d, _), v in self.drafts.items() if d == domain]

    def mark_draft_sent(self, domain, asset_id):
        self.drafts[(domain, asset_id)]["sent_at"] = "2026-01-01T00:00:00+00:00"


def build_config(tmp_path, csv_content, domain="Test Domain", registry_key="", rules=None):
    csv_path = tmp_path / "assets.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    return AppConfig(
        domain=domain,
        source=SourceConfig(type="csv", path=str(csv_path), id_field="asset_id"),
        contact=ContactConfig(name_field="contact_name", email_field="contact_email"),
        rules=rules if rules is not None else [
            RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                       message="low margin", label="Margin", params={"min": 5}),
        ],
        excel=ExcelConfig(info_columns=[ColumnConfig(field="asset_id", label="ID")]),
        email=EmailConfig(subject_template="s", body_template="b"),
        registry_key=registry_key,
    )


def test_sync_registry_upserts_every_record(tmp_path):
    config = build_config(tmp_path, "asset_id,margin_pct,contact_name,contact_email\nAST-1,10,Dana,dana@example.com\n")
    client = FakeDBClient()

    count = sync_registry(client, config)

    assert count == 1
    assert client.get_assets("Test Domain")[0]["record"]["margin_pct"] == "10"


def test_sync_registry_never_touches_existing_notes(tmp_path):
    config = build_config(tmp_path, "asset_id,margin_pct,contact_name,contact_email\nAST-1,10,Dana,dana@example.com\n")
    client = FakeDBClient()
    sync_registry(client, config)
    save_note(client, "Test Domain", "AST-1", {"Notes": "already following up"})

    sync_registry(client, config)  # re-sync

    assets = client.get_assets("Test Domain")
    assert assets[0]["notes"] == {"Notes": "already following up"}


def test_sync_registry_applies_latest_extracted_value_overlay(tmp_path):
    """The app's equivalent of the CLI's local extracted_values.csv overlay
    -- values the upload pipeline recorded in the extracted_values table
    must be layered onto the base CSV registry at sync time."""
    config = build_config(tmp_path, "asset_id,margin_pct,contact_name,contact_email\nAST-1,10,Dana,dana@example.com\n")
    client = FakeDBClient()
    client.append_extracted_value(
        "Test Domain", "AST-1", "margin_pct", "42", "doc.pdf", "high", "2026-08-01"
    )

    sync_registry(client, config)

    assert client.get_assets("Test Domain")[0]["record"]["margin_pct"] == "42"


def test_load_results_and_notes_runs_validator_against_db_records(tmp_path):
    config = build_config(tmp_path, "asset_id,margin_pct,contact_name,contact_email\nAST-1,2,Dana,dana@example.com\n")
    client = FakeDBClient()
    sync_registry(client, config)

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


def test_load_needs_review_excludes_filed_outcomes_and_other_domains():
    client = FakeDBClient()

    class Filed:
        source_filename = "AST-1_insurance_certificate.pdf"
        outcome = "filed"
        asset_id = "AST-1"
        document_type_rule_id = "insurance_doc_on_file"
        confidence = "high"

    class Pending:
        source_filename = "mystery_scan.pdf"
        outcome = "needs_review"
        asset_id = None
        document_type_rule_id = None
        confidence = "low"

    record_extraction_outcome(client, "Test Domain", Filed())
    record_extraction_outcome(client, "Test Domain", Pending())
    record_extraction_outcome(client, "Other Domain", Pending())

    pending = load_needs_review(client, "Test Domain")

    assert len(pending) == 1
    assert pending[0]["source_filename"] == "mystery_scan.pdf"


def test_two_lenses_sharing_a_registry_key_see_the_same_synced_assets(tmp_path):
    """The core "one filing cabinet, different views" behavior: lens A syncs
    the shared pool; lens B (different domain, same registry_key, same
    underlying CSV) must see those same assets immediately, with no sync of
    its own."""
    csv_content = "asset_id,margin_pct,downtime_hours,contact_name,contact_email\nAST-1,2,150,Dana,dana@example.com\n"

    lens_a = build_config(
        tmp_path, csv_content, domain="Energy — Compliance", registry_key="energy_assets",
        rules=[RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                           message="low margin", label="Margin", params={"min": 5})],
    )
    lens_b = build_config(
        tmp_path, csv_content, domain="Energy — Performance", registry_key="energy_assets",
        rules=[RuleConfig(id="downtime_max", field="downtime_hours", type="max_value", severity="warning",
                           message="too much downtime", label="Downtime", params={"max": 100})],
    )

    client = FakeDBClient()
    sync_registry(client, lens_a)  # sync under lens A only

    # Lens B never synced, but shares the registry key -- must see AST-1.
    results_b, _ = load_results_and_notes(client, lens_b)
    assert len(results_b) == 1
    assert results_b[0].asset_id == "AST-1"
    assert results_b[0].violations  # downtime 150 > 100, per lens B's own rules

    # A note saved under lens A must be visible reading through lens B too.
    save_note(client, lens_a.registry_key, "AST-1", {"Notes": "flagged by ops"})
    _, notes_b = load_results_and_notes(client, lens_b)
    assert notes_b["AST-1"] == {"Notes": "flagged by ops"}


def test_save_drafts_then_load_pending_drafts_round_trips():
    from compliance_tracker.email_drafter import DraftedEmail

    client = FakeDBClient()
    draft = DraftedEmail(
        asset_id="AST-1", to_name="Dana", to_email="dana@example.com",
        subject="Action Required", body="Please resolve...", file_path="unused",
    )

    save_drafts(client, "Test Domain", [draft])
    pending = load_pending_drafts(client, "Test Domain")

    assert len(pending) == 1
    assert pending[0]["asset_id"] == "AST-1"
    assert pending[0]["subject"] == "Action Required"
    assert pending[0]["sent_at"] is None


def test_load_pending_drafts_excludes_sent_ones():
    from compliance_tracker.email_drafter import DraftedEmail

    client = FakeDBClient()
    save_drafts(client, "Test Domain", [
        DraftedEmail(asset_id="AST-1", to_name="Dana", to_email="dana@example.com",
                     subject="s1", body="b1", file_path="unused"),
        DraftedEmail(asset_id="AST-2", to_name="Sam", to_email="sam@example.com",
                     subject="s2", body="b2", file_path="unused"),
    ])

    mark_draft_sent(client, "Test Domain", "AST-1")
    pending = load_pending_drafts(client, "Test Domain")

    assert len(pending) == 1
    assert pending[0]["asset_id"] == "AST-2"


def test_save_drafts_overwrites_earlier_unsent_draft_for_same_asset():
    from compliance_tracker.email_drafter import DraftedEmail

    client = FakeDBClient()
    save_drafts(client, "Test Domain", [
        DraftedEmail(asset_id="AST-1", to_name="Dana", to_email="dana@example.com",
                     subject="old subject", body="old body", file_path="unused"),
    ])
    save_drafts(client, "Test Domain", [
        DraftedEmail(asset_id="AST-1", to_name="Dana", to_email="dana@example.com",
                     subject="new subject", body="new body", file_path="unused"),
    ])

    pending = load_pending_drafts(client, "Test Domain")

    assert len(pending) == 1
    assert pending[0]["subject"] == "new subject"
