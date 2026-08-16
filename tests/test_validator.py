from compliance_tracker.config_schema import (
    AppConfig,
    ColumnConfig,
    ContactConfig,
    EmailConfig,
    ExcelConfig,
    RuleConfig,
    SourceConfig,
)
from compliance_tracker.validator import COMPLIANT, FLAGGED, validate_assets


def build_config(tmp_path, csv_content, rules):
    csv_path = tmp_path / "assets.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    return AppConfig(
        domain="Test Domain",
        source=SourceConfig(type="csv", path=str(csv_path), id_field="asset_id"),
        contact=ContactConfig(name_field="contact_name", email_field="contact_email"),
        rules=rules,
        excel=ExcelConfig(info_columns=[ColumnConfig(field="asset_id", label="ID")]),
        email=EmailConfig(subject_template="Subject {asset_id}", body_template="{issues_list}"),
    )


def test_validate_assets_marks_compliant_asset_with_no_violations(tmp_path):
    csv_content = "asset_id,margin_pct,contact_name,contact_email\nAST-1,10,Dana,dana@example.com\n"
    rules = [
        RuleConfig(
            id="margin_min", field="margin_pct", type="min_value", severity="warning",
            message="low margin", label="Margin", params={"min": 5},
        )
    ]
    config = build_config(tmp_path, csv_content, rules)
    results = validate_assets(config)

    assert len(results) == 1
    assert results[0].compliance_status == COMPLIANT
    assert results[0].critical_count == 0
    assert results[0].warning_count == 0


def test_validate_assets_flags_asset_and_counts_by_severity(tmp_path):
    csv_content = (
        "asset_id,margin_pct,permit_doc_ref,contact_name,contact_email\n"
        "AST-1,2,,Dana,dana@example.com\n"
    )
    rules = [
        RuleConfig(
            id="margin_min", field="margin_pct", type="min_value", severity="warning",
            message="low margin", label="Margin", params={"min": 5},
        ),
        RuleConfig(
            id="has_permit", field="permit_doc_ref", type="required", severity="critical",
            message="missing permit", label="Permit", params={},
        ),
    ]
    config = build_config(tmp_path, csv_content, rules)
    results = validate_assets(config)

    assert len(results) == 1
    result = results[0]
    assert result.compliance_status == FLAGGED
    assert result.critical_count == 1
    assert result.warning_count == 1
    assert {v.rule_id for v in result.violations} == {"margin_min", "has_permit"}


def test_validate_assets_handles_multiple_assets_independently(tmp_path):
    csv_content = (
        "asset_id,margin_pct,contact_name,contact_email\n"
        "AST-1,10,Dana,dana@example.com\n"
        "AST-2,1,Ben,ben@example.com\n"
    )
    rules = [
        RuleConfig(
            id="margin_min", field="margin_pct", type="min_value", severity="warning",
            message="low margin", label="Margin", params={"min": 5},
        ),
    ]
    config = build_config(tmp_path, csv_content, rules)
    results = validate_assets(config)

    by_id = {r.asset_id: r for r in results}
    assert by_id["AST-1"].compliance_status == COMPLIANT
    assert by_id["AST-2"].compliance_status == FLAGGED


def test_display_fields_includes_computed_columns(tmp_path):
    csv_content = "asset_id,margin_pct,contact_name,contact_email\nAST-1,2,Dana,dana@example.com\n"
    rules = [
        RuleConfig(
            id="margin_min", field="margin_pct", type="min_value", severity="warning",
            message="low margin", label="Margin", params={"min": 5},
        ),
    ]
    config = build_config(tmp_path, csv_content, rules)
    result = validate_assets(config)[0]
    fields = result.display_fields()

    assert fields["compliance_status"] == FLAGGED
    assert fields["critical_count"] == "0"
    assert fields["warning_count"] == "1"
    assert fields["margin_pct"] == "2"
