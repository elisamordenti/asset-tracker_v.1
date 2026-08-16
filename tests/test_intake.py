from unittest.mock import patch

from compliance_tracker.config_schema import (
    AppConfig,
    ColumnConfig,
    ContactConfig,
    EmailConfig,
    ExcelConfig,
    RuleConfig,
    SourceConfig,
)
from compliance_tracker.extracted_values import load_latest_values
from compliance_tracker.extraction import _RawExtraction
from compliance_tracker.intake import run_intake


def build_config(tmp_path, archive_dir):
    csv_path = tmp_path / "assets.csv"
    csv_path.write_text("asset_id,contact_name,contact_email\nAST-1,Dana,dana@example.com\n", encoding="utf-8")
    return AppConfig(
        domain="Test",
        source=SourceConfig(type="csv", path=str(csv_path), id_field="asset_id"),
        contact=ContactConfig(name_field="contact_name", email_field="contact_email"),
        rules=[
            RuleConfig(
                id="insurance_doc_on_file", type="document_on_file", severity="critical",
                label="Insurance Cert on File", message="missing",
                params={
                    "directory": str(archive_dir),
                    "filename_pattern": "{asset_id}_insurance_certificate.pdf",
                    "extractable_fields": [{"field": "insurance_expiry", "type": "date"}],
                },
            ),
        ],
        excel=ExcelConfig(info_columns=[ColumnConfig(field="asset_id", label="ID")]),
        email=EmailConfig(subject_template="s", body_template="b"),
    )


class FakeLLMClient:
    def __init__(self, response):
        self.response = response

    def parse_extraction(self, system, text):
        return self.response


def test_confident_match_is_filed_and_logged(tmp_path):
    archive_dir = tmp_path / "archive"
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    incoming = inbox_dir / "scan_whatever.pdf"
    incoming.write_bytes(b"%PDF-1.4\n%%EOF")

    config = build_config(tmp_path, archive_dir)
    fake = FakeLLMClient(_RawExtraction(
        asset_id="AST-1", document_type="insurance_doc_on_file", confidence="high",
        fields=[{"field": "insurance_expiry", "value": "2027-01-15"}],
    ))

    with patch("compliance_tracker.intake.extract_text", return_value="dummy text"):
        summary = run_intake(
            config, inbox_dir, tmp_path / "extracted_values.csv", tmp_path / "extraction_log.csv", client=fake
        )

    assert len(summary.filed) == 1
    assert len(summary.needs_review) == 0
    assert not incoming.exists()  # moved out of the inbox
    assert (archive_dir / "AST-1_insurance_certificate.pdf").exists()

    latest = load_latest_values(tmp_path / "extracted_values.csv")
    assert latest["AST-1"]["insurance_expiry"] == "2027-01-15"

    log_rows = (tmp_path / "extraction_log.csv").read_text(encoding="utf-8")
    assert "filed" in log_rows


def test_low_confidence_stays_in_inbox_and_needs_review(tmp_path):
    archive_dir = tmp_path / "archive"
    inbox_dir = tmp_path / "inbox"
    inbox_dir.mkdir()
    incoming = inbox_dir / "mystery.pdf"
    incoming.write_bytes(b"%PDF-1.4\n%%EOF")

    config = build_config(tmp_path, archive_dir)
    fake = FakeLLMClient(_RawExtraction(
        asset_id=None, document_type=None, confidence="low", fields=[],
    ))

    with patch("compliance_tracker.intake.extract_text", return_value="dummy text"):
        summary = run_intake(
            config, inbox_dir, tmp_path / "extracted_values.csv", tmp_path / "extraction_log.csv", client=fake
        )

    assert len(summary.filed) == 0
    assert len(summary.needs_review) == 1
    assert incoming.exists()  # left in place, never guessed
    assert not archive_dir.exists() or not any(archive_dir.iterdir())


def test_missing_inbox_dir_returns_empty_summary(tmp_path):
    config = build_config(tmp_path, tmp_path / "archive")
    fake = FakeLLMClient(_RawExtraction(asset_id=None, document_type=None, confidence="low", fields=[]))

    summary = run_intake(
        config, tmp_path / "no_such_inbox", tmp_path / "extracted_values.csv", tmp_path / "extraction_log.csv",
        client=fake,
    )

    assert summary.outcomes == []
