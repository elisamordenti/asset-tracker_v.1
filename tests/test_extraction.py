"""No `anthropic` import anywhere in this file -- classify_and_extract is
tested purely against the LLMClient interface, via this in-memory fake."""

from compliance_tracker.config_schema import (
    AppConfig,
    ContactConfig,
    EmailConfig,
    ExcelConfig,
    ColumnConfig,
    RuleConfig,
    SourceConfig,
)
from compliance_tracker.extraction import (
    _RawExtraction,
    build_document_type_candidates,
    classify_and_extract,
    extract_text,
)


class FakeLLMClient:
    def __init__(self, response: _RawExtraction):
        self.response = response
        self.last_system = None
        self.last_text = None

    def parse_extraction(self, system, text):
        self.last_system = system
        self.last_text = text
        return self.response


def build_config_with_document_rules():
    csv_path_rules = [
        RuleConfig(
            id="insurance_doc_on_file", type="document_on_file", severity="critical",
            label="Insurance Cert on File", message="missing",
            params={
                "directory": "some/dir",
                "filename_pattern": "{asset_id}_insurance_certificate.pdf",
                "extractable_fields": [{"field": "insurance_expiry", "type": "date"}],
            },
        ),
        RuleConfig(
            id="permit_doc_on_file", type="document_on_file", severity="critical",
            label="Permit on File", message="missing",
            params={"directory": "some/dir", "filename_pattern": "{asset_id}_permit.pdf"},
        ),
    ]
    return AppConfig(
        domain="Test",
        source=SourceConfig(type="csv", path="assets.csv", id_field="asset_id"),
        contact=ContactConfig(name_field="contact_name", email_field="contact_email"),
        rules=csv_path_rules,
        excel=ExcelConfig(info_columns=[ColumnConfig(field="asset_id", label="ID")]),
        email=EmailConfig(subject_template="s", body_template="b"),
    )


def test_build_document_type_candidates_reads_document_on_file_rules_only():
    config = build_config_with_document_rules()
    candidates = build_document_type_candidates(config)

    assert {c.rule_id for c in candidates} == {"insurance_doc_on_file", "permit_doc_on_file"}
    insurance = next(c for c in candidates if c.rule_id == "insurance_doc_on_file")
    assert insurance.extractable_fields == [{"field": "insurance_expiry", "type": "date"}]
    permit = next(c for c in candidates if c.rule_id == "permit_doc_on_file")
    assert permit.extractable_fields == []


def test_classify_and_extract_confident_match():
    config = build_config_with_document_rules()
    candidates = build_document_type_candidates(config)
    fake = FakeLLMClient(_RawExtraction(
        asset_id="AST-1",
        document_type="insurance_doc_on_file",
        confidence="high",
        fields=[{"field": "insurance_expiry", "value": "2027-01-15"}],
    ))

    result = classify_and_extract("some pdf text", ["AST-1", "AST-2"], candidates, client=fake)

    assert result.asset_id == "AST-1"
    assert result.document_type_rule_id == "insurance_doc_on_file"
    assert result.confidence == "high"
    assert result.fields == {"insurance_expiry": "2027-01-15"}


def test_classify_and_extract_rejects_unknown_asset_id():
    config = build_config_with_document_rules()
    candidates = build_document_type_candidates(config)
    fake = FakeLLMClient(_RawExtraction(
        asset_id="AST-999",  # not in known list
        document_type="insurance_doc_on_file",
        confidence="high",
        fields=[],
    ))

    result = classify_and_extract("text", ["AST-1", "AST-2"], candidates, client=fake)

    assert result.asset_id is None
    assert result.confidence == "low"  # downgraded


def test_classify_and_extract_rejects_unknown_document_type():
    config = build_config_with_document_rules()
    candidates = build_document_type_candidates(config)
    fake = FakeLLMClient(_RawExtraction(
        asset_id="AST-1",
        document_type="not_a_real_rule",
        confidence="high",
        fields=[],
    ))

    result = classify_and_extract("text", ["AST-1"], candidates, client=fake)

    assert result.document_type_rule_id is None
    assert result.confidence == "low"


def test_classify_and_extract_drops_malformed_date_field():
    config = build_config_with_document_rules()
    candidates = build_document_type_candidates(config)
    fake = FakeLLMClient(_RawExtraction(
        asset_id="AST-1",
        document_type="insurance_doc_on_file",
        confidence="high",
        fields=[{"field": "insurance_expiry", "value": "not-a-date"}],
    ))

    result = classify_and_extract("text", ["AST-1"], candidates, client=fake)

    assert result.fields == {}  # malformed date silently dropped, not written


def test_classify_and_extract_keeps_field_with_no_declared_type():
    config = build_config_with_document_rules()
    candidates = build_document_type_candidates(config)
    fake = FakeLLMClient(_RawExtraction(
        asset_id="AST-1",
        document_type="permit_doc_on_file",
        confidence="medium",
        fields=[{"field": "permit_number", "value": "anything goes"}],
    ))

    result = classify_and_extract("text", ["AST-1"], candidates, client=fake)

    assert result.fields == {"permit_number": "anything goes"}


def test_extract_text_reads_excel_workbook(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Technical Schedule"
    ws.append(["Asset ID", "Certification Expiry"])
    ws.append(["AST-004", "2028-01-15"])
    path = tmp_path / "schedule.xlsx"
    wb.save(path)

    text = extract_text(path)

    assert "[Sheet: Technical Schedule]" in text
    assert "Asset ID | Certification Expiry" in text
    assert "AST-004 | 2028-01-15" in text


def test_extract_text_raises_for_unsupported_extension(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello", encoding="utf-8")

    try:
        extract_text(path)
        assert False, "expected ValueError"
    except ValueError as e:
        assert ".txt" in str(e)
