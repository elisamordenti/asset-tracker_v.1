"""Tests the eval harness's comparison/verdict logic against a FakeLLMClient
-- no live API calls, same pattern as test_extraction.py and test_intake.py.
This is what proves run_eval.py's bucketing is doing the right thing before
it's ever pointed at a real model."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "eval"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from generate_sample_documents import build_minimal_pdf  # noqa: E402

from compliance_tracker.extraction import _RawExtraction  # noqa: E402
from run_eval import _write_results, evaluate_case  # noqa: E402


def write_pdf(path: Path, lines: list[str]) -> None:
    path.write_bytes(build_minimal_pdf(lines))


def _make_candidates():
    from compliance_tracker.config_schema import (
        AppConfig,
        ColumnConfig,
        ContactConfig,
        EmailConfig,
        ExcelConfig,
        RuleConfig,
        SourceConfig,
    )
    from compliance_tracker.extraction import build_document_type_candidates

    config = AppConfig(
        domain="Test",
        source=SourceConfig(type="csv", path="assets.csv", id_field="asset_id"),
        contact=ContactConfig(name_field="contact_name", email_field="contact_email"),
        rules=[
            RuleConfig(
                id="insurance_doc_on_file", type="document_on_file", severity="critical",
                label="Insurance Cert on File", message="missing",
                params={
                    "directory": "some/dir",
                    "filename_pattern": "{asset_id}_insurance_certificate.pdf",
                    "extractable_fields": [{"field": "insurance_expiry", "type": "date"}],
                },
            ),
        ],
        excel=ExcelConfig(info_columns=[ColumnConfig(field="asset_id", label="ID")]),
        email=EmailConfig(subject_template="s", body_template="b"),
    )
    return build_document_type_candidates(config)


class FakeLLMClient:
    def __init__(self, response):
        self.response = response

    def parse_extraction(self, system, content):
        return self.response


def _row(fixture_file, expected_asset_id, expected_document_type, expected_resolution, field, expected_value, failure_mode):
    return {
        "fixture_file": fixture_file,
        "expected_asset_id": expected_asset_id,
        "expected_document_type": expected_document_type,
        "expected_resolution": expected_resolution,
        "field": field,
        "expected_value": expected_value,
        "failure_mode": failure_mode,
    }


def test_evaluate_case_correct_and_confident_is_match(tmp_path, monkeypatch):
    import run_eval

    fixture = tmp_path / "clean.pdf"
    write_pdf(fixture, ["Insurance Certificate", "Asset / Entity: AST-1", "Policy Expiry: 2027-01-15"])
    monkeypatch.setattr(run_eval, "FIXTURES_DIR", tmp_path)

    row = _row(
        "clean.pdf", "AST-1", "insurance_doc_on_file", "llm", "insurance_expiry", "2027-01-15", "clean_baseline",
    )
    fake = FakeLLMClient(_RawExtraction(
        asset_id="AST-1", document_type="insurance_doc_on_file", confidence="high",
        fields=[{"field": "insurance_expiry", "value": "2027-01-15", "citation": "Policy Expiry: 2027-01-15"}],
    ))

    result = evaluate_case(row, _make_candidates(), ["AST-1"], client=fake)

    assert result.verdict == "MATCH"
    assert result.citation_status == "verified"


def test_evaluate_case_wrong_but_confident_is_false_negative(tmp_path, monkeypatch):
    import run_eval

    fixture = tmp_path / "wrong.pdf"
    write_pdf(fixture, ["Insurance Certificate", "Asset / Entity: AST-1", "Policy Expiry: 2027-01-15"])
    monkeypatch.setattr(run_eval, "FIXTURES_DIR", tmp_path)

    row = _row(
        "wrong.pdf", "AST-1", "insurance_doc_on_file", "llm", "insurance_expiry", "2027-01-15", "malformed_date",
    )
    fake = FakeLLMClient(_RawExtraction(
        asset_id="AST-1", document_type="insurance_doc_on_file", confidence="high",
        fields=[{"field": "insurance_expiry", "value": "2099-12-31", "citation": "text not in the document"}],
    ))

    result = evaluate_case(row, _make_candidates(), ["AST-1"], client=fake)

    assert result.verdict == "FALSE_NEGATIVE"
    assert result.citation_status == "unverified"


def test_evaluate_case_correct_but_underconfident_is_false_positive(tmp_path, monkeypatch):
    import run_eval

    fixture = tmp_path / "cautious.pdf"
    write_pdf(fixture, ["Insurance Certificate", "Asset / Entity: AST-1", "Policy Expiry: 2027-01-15"])
    monkeypatch.setattr(run_eval, "FIXTURES_DIR", tmp_path)

    row = _row(
        "cautious.pdf", "AST-1", "insurance_doc_on_file", "llm", "insurance_expiry", "2027-01-15", "unusual_label",
    )
    fake = FakeLLMClient(_RawExtraction(
        asset_id="AST-1", document_type="insurance_doc_on_file", confidence="low",
        fields=[{"field": "insurance_expiry", "value": "2027-01-15", "citation": "Policy Expiry: 2027-01-15"}],
    ))

    result = evaluate_case(row, _make_candidates(), ["AST-1"], client=fake)

    assert result.verdict == "FALSE_POSITIVE"


def test_evaluate_case_genuinely_ambiguous_and_abstained_is_match(tmp_path, monkeypatch):
    import run_eval

    fixture = tmp_path / "ambiguous.pdf"
    write_pdf(fixture, ["Capitalization Table Export", "Entity: PC-005"])
    monkeypatch.setattr(run_eval, "FIXTURES_DIR", tmp_path)

    row = _row("ambiguous.pdf", "", "", "llm", "", "", "wrong_domain")
    fake = FakeLLMClient(_RawExtraction(asset_id=None, document_type=None, confidence="low", fields=[]))

    result = evaluate_case(row, _make_candidates(), ["AST-1"], client=fake)

    assert result.verdict == "MATCH"
    assert result.citation_status == "n/a"


def test_write_results_produces_a_summary_with_all_verdicts(tmp_path, monkeypatch):
    import run_eval

    results_path = tmp_path / "results.md"
    monkeypatch.setattr(run_eval, "RESULTS_PATH", results_path)

    fixture = tmp_path / "clean.pdf"
    write_pdf(fixture, ["Insurance Certificate", "Asset / Entity: AST-1", "Policy Expiry: 2027-01-15"])
    monkeypatch.setattr(run_eval, "FIXTURES_DIR", tmp_path)

    row = _row(
        "clean.pdf", "AST-1", "insurance_doc_on_file", "llm", "insurance_expiry", "2027-01-15", "clean_baseline",
    )
    fake = FakeLLMClient(_RawExtraction(
        asset_id="AST-1", document_type="insurance_doc_on_file", confidence="high",
        fields=[{"field": "insurance_expiry", "value": "2027-01-15", "citation": "Policy Expiry: 2027-01-15"}],
    ))
    case = evaluate_case(row, _make_candidates(), ["AST-1"], client=fake)

    _write_results([case])

    content = results_path.read_text(encoding="utf-8")
    assert "MATCH" in content
    assert "clean_baseline" in content
    assert "verified" in content
