from unittest.mock import MagicMock

import pytest

from compliance_tracker.config_schema import (
    AppConfig,
    ColumnConfig,
    ContactConfig,
    EmailConfig,
    ExcelConfig,
    RuleConfig,
    SourceConfig,
)
from compliance_tracker.email_drafter import draft_emails, send_drafted_emails
from compliance_tracker.validator import validate_assets


def build_config(tmp_path, csv_content, rules):
    csv_path = tmp_path / "assets.csv"
    csv_path.write_text(csv_content, encoding="utf-8")
    return AppConfig(
        domain="Test Domain",
        source=SourceConfig(type="csv", path=str(csv_path), id_field="asset_id"),
        contact=ContactConfig(name_field="contact_name", email_field="contact_email"),
        rules=rules,
        excel=ExcelConfig(
            summary_columns=[ColumnConfig(field="asset_id", label="ID")],
            detail_columns=[ColumnConfig(field="asset_id", label="ID")],
        ),
        email=EmailConfig(
            subject_template="Action Required: {asset_id}",
            body_template="Hi {contact_name},\n\n{issues_list}\n",
        ),
    )


def test_draft_emails_only_writes_for_flagged_assets(tmp_path):
    csv_content = (
        "asset_id,margin_pct,contact_name,contact_email\n"
        "AST-1,10,Dana,dana@example.com\n"
        "AST-2,1,Ben,ben@example.com\n"
    )
    rules = [
        RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                   message="Margin {value}% too low", params={"min": 5}),
    ]
    config = build_config(tmp_path, csv_content, rules)
    results = validate_assets(config)

    drafts = draft_emails(config, results, tmp_path / "emails")

    assert len(drafts) == 1
    assert drafts[0].asset_id == "AST-2"
    assert drafts[0].file_path.exists()
    assert not (tmp_path / "emails" / "AST-1.txt").exists()


def test_draft_email_content_includes_contact_and_issues(tmp_path):
    csv_content = "asset_id,margin_pct,contact_name,contact_email\nAST-1,1,Ben,ben@example.com\n"
    rules = [
        RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                   message="Margin {value}% too low", params={"min": 5}),
    ]
    config = build_config(tmp_path, csv_content, rules)
    results = validate_assets(config)
    drafts = draft_emails(config, results, tmp_path / "emails")

    content = drafts[0].file_path.read_text(encoding="utf-8")
    assert "Ben" in content
    assert "ben@example.com" in content
    assert "Margin 1% too low" in content


def test_send_drafted_emails_refuses_without_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("EMAIL_SEND_MODE", raising=False)
    with pytest.raises(RuntimeError, match="Refusing to send"):
        send_drafted_emails([])


def test_send_drafted_emails_refuses_with_missing_smtp_vars(monkeypatch):
    monkeypatch.setenv("EMAIL_SEND_MODE", "live")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with pytest.raises(RuntimeError, match="missing env var"):
        send_drafted_emails([])


def test_send_drafted_emails_sends_via_smtp_when_opted_in(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAIL_SEND_MODE", "live")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "bot@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")

    mock_smtp_instance = MagicMock()
    mock_smtp_cls = MagicMock()
    mock_smtp_cls.return_value.__enter__.return_value = mock_smtp_instance
    monkeypatch.setattr("compliance_tracker.email_drafter.smtplib.SMTP", mock_smtp_cls)

    csv_content = "asset_id,margin_pct,contact_name,contact_email\nAST-1,1,Ben,ben@example.com\n"
    rules = [
        RuleConfig(id="margin_min", field="margin_pct", type="min_value", severity="warning",
                   message="Margin {value}% too low", params={"min": 5}),
    ]
    config = build_config(tmp_path, csv_content, rules)
    results = validate_assets(config)
    drafts = draft_emails(config, results, tmp_path / "emails")

    sent = send_drafted_emails(drafts)

    assert sent == 1
    mock_smtp_instance.login.assert_called_once_with("bot@example.com", "secret")
    mock_smtp_instance.send_message.assert_called_once()
