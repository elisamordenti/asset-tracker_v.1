from datetime import date, timedelta

from compliance_tracker.config_schema import RuleConfig
from compliance_tracker.rules import RuleContext, evaluate_rule


def make_rule(**overrides):
    defaults = dict(
        id="test_rule",
        field="value",
        type="required",
        severity="critical",
        message="failed: {value}",
        label="Test Rule",
        params={},
    )
    defaults.update(overrides)
    return RuleConfig(**defaults)


def test_required_passes_when_present():
    rule = make_rule(type="required")
    assert evaluate_rule(rule, {"value": "PMT-1"}) is None


def test_required_fails_when_empty():
    rule = make_rule(type="required")
    result = evaluate_rule(rule, {"value": ""})
    assert result is not None
    assert result.severity == "critical"


def test_required_fails_when_missing_field():
    rule = make_rule(type="required")
    result = evaluate_rule(rule, {})
    assert result is not None


def test_not_expired_passes_for_future_date():
    rule = make_rule(type="not_expired", params={"max_age_days": 0})
    future = (date.today() + timedelta(days=10)).isoformat()
    assert evaluate_rule(rule, {"value": future}) is None


def test_not_expired_fails_for_past_date():
    rule = make_rule(type="not_expired", params={"max_age_days": 0})
    past = (date.today() - timedelta(days=1)).isoformat()
    result = evaluate_rule(rule, {"value": past})
    assert result is not None
    assert result.value == past


def test_not_expired_boundary_today_passes():
    rule = make_rule(type="not_expired", params={"max_age_days": 0})
    today = date.today().isoformat()
    assert evaluate_rule(rule, {"value": today}) is None


def test_not_expired_respects_grace_period():
    rule = make_rule(type="not_expired", params={"max_age_days": 45})
    within_grace = (date.today() - timedelta(days=45)).isoformat()
    beyond_grace = (date.today() - timedelta(days=46)).isoformat()
    assert evaluate_rule(rule, {"value": within_grace}) is None
    assert evaluate_rule(rule, {"value": beyond_grace}) is not None


def test_not_expired_fails_on_unparsable_date():
    rule = make_rule(type="not_expired", params={"max_age_days": 0})
    result = evaluate_rule(rule, {"value": "not-a-date"})
    assert result is not None


def test_min_value_boundary_passes_at_exact_min():
    rule = make_rule(type="min_value", params={"min": 5})
    assert evaluate_rule(rule, {"value": "5"}) is None


def test_min_value_fails_below_min():
    rule = make_rule(type="min_value", params={"min": 5})
    result = evaluate_rule(rule, {"value": "4.9"})
    assert result is not None


def test_min_value_fails_on_non_numeric():
    rule = make_rule(type="min_value", params={"min": 5})
    result = evaluate_rule(rule, {"value": "n/a"})
    assert result is not None


def test_max_value_boundary_passes_at_exact_max():
    rule = make_rule(type="max_value", params={"max": 100})
    assert evaluate_rule(rule, {"value": "100"}) is None


def test_max_value_fails_above_max():
    rule = make_rule(type="max_value", params={"max": 100})
    result = evaluate_rule(rule, {"value": "100.1"})
    assert result is not None


def test_allowed_values_passes_for_member():
    rule = make_rule(type="allowed_values", params={"values": ["current", "scheduled"]})
    assert evaluate_rule(rule, {"value": "current"}) is None


def test_allowed_values_fails_for_non_member():
    rule = make_rule(type="allowed_values", params={"values": ["current", "scheduled"]})
    result = evaluate_rule(rule, {"value": "overdue"})
    assert result is not None


def test_regex_match_passes_for_matching_format():
    rule = make_rule(type="regex_match", params={"pattern": "^PMT-[0-9]{4,}$"})
    assert evaluate_rule(rule, {"value": "PMT-1001"}) is None


def test_regex_match_fails_for_wrong_format():
    rule = make_rule(type="regex_match", params={"pattern": "^PMT-[0-9]{4,}$"})
    result = evaluate_rule(rule, {"value": "PMT9"})
    assert result is not None


def test_regex_match_fails_for_empty_value():
    rule = make_rule(type="regex_match", params={"pattern": "^PMT-[0-9]{4,}$"})
    result = evaluate_rule(rule, {"value": ""})
    assert result is not None


def test_message_template_can_reference_other_record_fields():
    rule = make_rule(type="required", field="permit_doc_ref", message="{asset_name} is missing a permit")
    result = evaluate_rule(rule, {"permit_doc_ref": "", "asset_name": "Solar Farm Alpha"})
    assert result.message == "Solar Farm Alpha is missing a permit"


def test_document_on_file_passes_when_file_exists():
    rule = make_rule(
        type="document_on_file", field=None,
        params={"directory": "some/dir", "filename_pattern": "{asset_id}_permit.pdf"},
    )
    context = RuleContext(archive_index={"some/dir": {"AST-1_permit.pdf"}})
    assert evaluate_rule(rule, {"asset_id": "AST-1"}, context) is None


def test_document_on_file_fails_when_file_missing():
    rule = make_rule(
        type="document_on_file", field=None,
        params={"directory": "some/dir", "filename_pattern": "{asset_id}_permit.pdf"},
    )
    result = evaluate_rule(rule, {"asset_id": "AST-1"})  # no context -- empty archive index
    assert result is not None
    assert result.value == "AST-1_permit.pdf"


def test_document_on_file_resolves_pattern_from_whole_record():
    rule = make_rule(
        type="document_on_file", field=None,
        params={"directory": "/nonexistent", "filename_pattern": "{company_id}_cap_table.pdf"},
        message="Missing: {value}",
    )
    result = evaluate_rule(rule, {"company_id": "PC-1"})
    assert result.message == "Missing: PC-1_cap_table.pdf"
