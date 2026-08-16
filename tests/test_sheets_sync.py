"""No `gspread` import anywhere in this file -- sync_tracker is tested purely
against the SheetsClient interface, via this in-memory fake."""

from compliance_tracker.config_schema import (
    AppConfig,
    ColumnConfig,
    ContactConfig,
    EmailConfig,
    ExcelConfig,
    RuleConfig,
    SourceConfig,
)
from compliance_tracker.sheets_sync import sync_tracker
from compliance_tracker.validator import validate_assets


class FakeSheetsClient:
    def __init__(self):
        self.values: list[list] = []

    def get_all_values(self) -> list[list[str]]:
        return self.values

    def update(self, values: list[list]) -> None:
        self.values = [[str(v) for v in row] for row in values]

    def clear(self) -> None:
        self.values = []


def build_config(tmp_path, csv_content, rules, notes_columns=None):
    csv_path = tmp_path / "assets.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    return AppConfig(
        domain="Test Domain",
        source=SourceConfig(type="csv", path=str(csv_path), id_field="asset_id"),
        contact=ContactConfig(name_field="contact_name", email_field="contact_email"),
        rules=rules,
        excel=ExcelConfig(
            info_columns=[ColumnConfig(field="asset_id", label="ID")],
            notes_columns=notes_columns or [],
        ),
        email=EmailConfig(subject_template="Subject {asset_id}", body_template="{issues_list}"),
    )


def test_sync_writes_header_and_rows(tmp_path):
    csv_content = "asset_id,margin_pct,contact_name,contact_email\nAST-1,2,Dana,dana@example.com\n"
    rules = [
        RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                   message="low margin", label="Margin", params={"min": 5}),
    ]
    config = build_config(tmp_path, csv_content, rules)
    results = validate_assets(config)
    client = FakeSheetsClient()

    sync_tracker(client, config, results, {})

    assert client.values[0] == ["ID", "Margin", "Next Deadline", "Last Reminder Sent", "Reminder Count", "Status"]
    assert client.values[1][0] == "AST-1"
    assert client.values[1][-1] == "FLAGGED"


def test_sync_clears_before_writing(tmp_path):
    csv_content = "asset_id,margin_pct,contact_name,contact_email\nAST-1,10,Dana,dana@example.com\n"
    rules = [
        RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                   message="low margin", label="Margin", params={"min": 5}),
    ]
    config = build_config(tmp_path, csv_content, rules)
    results = validate_assets(config)
    client = FakeSheetsClient()
    client.values = [["stale", "data", "from", "a", "prior", "domain"]]

    sync_tracker(client, config, results, {})

    assert client.values[0] == ["ID", "Margin", "Next Deadline", "Last Reminder Sent", "Reminder Count", "Status"]


def test_notes_preserved_across_sync_calls(tmp_path):
    csv_content = "asset_id,margin_pct,contact_name,contact_email\nAST-1,10,Dana,dana@example.com\n"
    rules = [
        RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                   message="low margin", label="Margin", params={"min": 5}),
    ]
    config = build_config(tmp_path, csv_content, rules, notes_columns=[ColumnConfig(field="notes", label="Notes")])
    results = validate_assets(config)
    client = FakeSheetsClient()

    sync_tracker(client, config, results, {})

    # Simulate a human editing the live sheet directly.
    headers = client.values[0]
    notes_idx = headers.index("Notes")
    client.values[1][notes_idx] = "Called client, awaiting reply"

    # Margin now fails harder -- rule column must refresh, note must survive.
    csv_content_v2 = "asset_id,margin_pct,contact_name,contact_email\nAST-1,1,Dana,dana@example.com\n"
    config_v2 = build_config(tmp_path, csv_content_v2, rules, notes_columns=[ColumnConfig(field="notes", label="Notes")])
    results_v2 = validate_assets(config_v2)
    sync_tracker(client, config_v2, results_v2, {})

    headers_v2 = client.values[0]
    row = dict(zip(headers_v2, client.values[1]))
    assert row["Notes"] == "Called client, awaiting reply"
    assert row["Margin"] == "1"


def test_sync_handles_never_before_synced_empty_sheet(tmp_path):
    csv_content = "asset_id,margin_pct,contact_name,contact_email\nAST-1,10,Dana,dana@example.com\n"
    rules = [
        RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                   message="low margin", label="Margin", params={"min": 5}),
    ]
    config = build_config(tmp_path, csv_content, rules, notes_columns=[ColumnConfig(field="notes", label="Notes")])
    results = validate_assets(config)
    client = FakeSheetsClient()  # never synced before -- empty

    sync_tracker(client, config, results, {})  # must not raise

    assert client.values[1][0] == "AST-1"


def test_asset_no_longer_present_drops_from_sheet(tmp_path):
    csv_content = (
        "asset_id,margin_pct,contact_name,contact_email\n"
        "AST-1,10,Dana,dana@example.com\n"
        "AST-2,10,Ben,ben@example.com\n"
    )
    rules = [
        RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                   message="low margin", label="Margin", params={"min": 5}),
    ]
    config = build_config(tmp_path, csv_content, rules)
    results = validate_assets(config)
    client = FakeSheetsClient()
    sync_tracker(client, config, results, {})

    csv_content_v2 = "asset_id,margin_pct,contact_name,contact_email\nAST-1,10,Dana,dana@example.com\n"
    config_v2 = build_config(tmp_path, csv_content_v2, rules)
    results_v2 = validate_assets(config_v2)
    sync_tracker(client, config_v2, results_v2, {})

    asset_ids = [row[0] for row in client.values[1:]]
    assert asset_ids == ["AST-1"]
