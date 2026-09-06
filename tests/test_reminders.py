"""No `supabase`/`smtplib` network calls anywhere here -- run_reminder_cycle
is tested purely against a minimal in-memory DBClient fake, and email
sending is exercised only through its env-var-gated refusal path."""

from compliance_tracker.config_schema import (
    AppConfig,
    ColumnConfig,
    ContactConfig,
    EmailConfig,
    ExcelConfig,
    RuleConfig,
    SourceConfig,
)
from compliance_tracker.reminders import run_reminder_cycle


class FakeDBClient:
    def __init__(self, assets):
        self._assets = assets  # [{"asset_id": ..., "record": ..., "notes": ...}]
        self.reminders = []

    def get_assets(self, domain):
        return self._assets

    def append_reminder(self, domain, asset_id, sent_date, reminder_number):
        self.reminders.append({"asset_id": asset_id, "date": sent_date, "reminder_number": reminder_number})

    def get_reminders(self, domain):
        return self.reminders


def build_config():
    return AppConfig(
        domain="Test",
        source=SourceConfig(type="csv", path="unused.csv", id_field="asset_id"),
        contact=ContactConfig(name_field="contact_name", email_field="contact_email"),
        rules=[
            RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                       message="low margin", label="Margin", params={"min": 5}),
        ],
        excel=ExcelConfig(info_columns=[ColumnConfig(field="asset_id", label="ID")]),
        email=EmailConfig(subject_template="Subject {asset_id}", body_template="{issues_list}"),
    )


def make_asset(asset_id, margin_pct):
    return {
        "asset_id": asset_id,
        "record": {"asset_id": asset_id, "margin_pct": margin_pct, "contact_name": "Dana", "contact_email": "dana@example.com"},
        "notes": {},
    }


def test_run_reminder_cycle_drafts_only_flagged_assets(tmp_path):
    config = build_config()
    client = FakeDBClient([make_asset("AST-1", "2"), make_asset("AST-2", "10")])

    outcome = run_reminder_cycle(config, client, tmp_path / "output")

    assert outcome.flagged_count == 1
    assert len(outcome.drafts) == 1
    assert outcome.drafts[0].asset_id == "AST-1"
    assert outcome.sent is None  # send=False by default -- never attempted


def test_run_reminder_cycle_records_reminder_history_across_calls(tmp_path):
    config = build_config()
    client = FakeDBClient([make_asset("AST-1", "2")])

    run_reminder_cycle(config, client, tmp_path / "output")
    run_reminder_cycle(config, client, tmp_path / "output")

    assert len(client.reminders) == 2
    assert client.reminders[1]["reminder_number"] == 2


def test_run_reminder_cycle_send_without_env_vars_reports_error_not_raise(tmp_path, monkeypatch):
    monkeypatch.delenv("EMAIL_SEND_MODE", raising=False)
    config = build_config()
    client = FakeDBClient([make_asset("AST-1", "2")])

    outcome = run_reminder_cycle(config, client, tmp_path / "output", send=True)

    assert outcome.sent is None
    assert "EMAIL_SEND_MODE" in outcome.send_error


def test_run_reminder_cycle_no_flagged_assets_drafts_nothing(tmp_path):
    config = build_config()
    client = FakeDBClient([make_asset("AST-1", "10")])

    outcome = run_reminder_cycle(config, client, tmp_path / "output")

    assert outcome.flagged_count == 0
    assert outcome.drafts == []
