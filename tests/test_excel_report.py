from openpyxl import load_workbook

from compliance_tracker.config_schema import (
    AppConfig,
    ColumnConfig,
    ContactConfig,
    EmailConfig,
    ExcelConfig,
    RuleConfig,
    SourceConfig,
)
from compliance_tracker.excel_report import generate_excel_report
from compliance_tracker.validator import validate_assets


def build_config(tmp_path, csv_content, rules, summary_columns, detail_columns, source_field="asset_id"):
    csv_path = tmp_path / "assets.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    return AppConfig(
        domain="Test Domain",
        source=SourceConfig(type="csv", path=str(csv_path), id_field=source_field),
        contact=ContactConfig(name_field="contact_name", email_field="contact_email"),
        rules=rules,
        excel=ExcelConfig(summary_columns=summary_columns, detail_columns=detail_columns),
        email=EmailConfig(subject_template="Subject {" + source_field + "}", body_template="{issues_list}"),
    )


def test_excel_report_headers_come_from_config_not_hardcoded(tmp_path):
    csv_content = "asset_id,margin_pct,contact_name,contact_email\nAST-1,2,Dana,dana@example.com\n"
    rules = [
        RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                   message="low margin", params={"min": 5}),
    ]
    summary_columns = [
        ColumnConfig(field="asset_id", label="Totally Custom Label"),
        ColumnConfig(field="compliance_status", label="Custom Status"),
    ]
    detail_columns = [ColumnConfig(field="margin_pct", label="Margin Custom")]
    config = build_config(tmp_path, csv_content, rules, summary_columns, detail_columns)

    results = validate_assets(config)
    output_path = generate_excel_report(config, results, tmp_path / "tracker.xlsx")

    wb = load_workbook(output_path)
    summary_headers = [c.value for c in wb["Summary"][1]]
    detail_headers = [c.value for c in wb["Asset Detail"][1]]

    assert summary_headers == ["Totally Custom Label", "Custom Status"]
    assert detail_headers == ["Margin Custom"]


def test_excel_report_has_three_sheets(tmp_path):
    csv_content = "asset_id,contact_name,contact_email\nAST-1,Dana,dana@example.com\n"
    config = build_config(
        tmp_path, csv_content,
        rules=[RuleConfig(id="r", field="asset_id", type="required", severity="warning",
                           message="m", params={})],
        summary_columns=[ColumnConfig(field="asset_id", label="ID")],
        detail_columns=[ColumnConfig(field="asset_id", label="ID")],
    )
    results = validate_assets(config)
    output_path = generate_excel_report(config, results, tmp_path / "tracker.xlsx")

    wb = load_workbook(output_path)
    assert wb.sheetnames == ["Summary", "Asset Detail", "Issues Log"]


def test_issues_log_has_one_row_per_violation(tmp_path):
    csv_content = (
        "asset_id,margin_pct,permit_doc_ref,contact_name,contact_email\n"
        "AST-1,2,,Dana,dana@example.com\n"
    )
    rules = [
        RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                   message="low margin", params={"min": 5}),
        RuleConfig(id="has_permit", field="permit_doc_ref", type="required", severity="critical",
                   message="missing permit", params={}),
    ]
    config = build_config(
        tmp_path, csv_content, rules,
        summary_columns=[ColumnConfig(field="asset_id", label="ID")],
        detail_columns=[ColumnConfig(field="asset_id", label="ID")],
    )
    results = validate_assets(config)
    output_path = generate_excel_report(config, results, tmp_path / "tracker.xlsx")

    wb = load_workbook(output_path)
    issues_ws = wb["Issues Log"]
    data_rows = list(issues_ws.iter_rows(min_row=2, values_only=True))
    assert len(data_rows) == 2
    rule_ids = {row[1] for row in data_rows}
    assert rule_ids == {"margin_min", "has_permit"}


def test_summary_sheet_sorts_flagged_before_compliant(tmp_path):
    csv_content = (
        "asset_id,margin_pct,contact_name,contact_email\n"
        "AST-1,10,Dana,dana@example.com\n"
        "AST-2,1,Ben,ben@example.com\n"
    )
    rules = [
        RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                   message="low margin", params={"min": 5}),
    ]
    config = build_config(
        tmp_path, csv_content, rules,
        summary_columns=[ColumnConfig(field="asset_id", label="ID"), ColumnConfig(field="compliance_status", label="Status")],
        detail_columns=[ColumnConfig(field="asset_id", label="ID")],
    )
    results = validate_assets(config)
    output_path = generate_excel_report(config, results, tmp_path / "tracker.xlsx")

    wb = load_workbook(output_path)
    rows = list(wb["Summary"].iter_rows(min_row=2, values_only=True))
    assert rows[0][0] == "AST-2"  # flagged asset listed first
    assert rows[0][1] == "FLAGGED"
    assert rows[1][1] == "COMPLIANT"
