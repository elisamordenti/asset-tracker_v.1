"""The fixed, generic rule-type vocabulary the validation engine understands.

This module is the entire "code" side of compliance logic. It never mentions
a domain (no "certification", no "runway", no field names) -- those live in
YAML. Each rule type here is a small, independently auditable check function;
a human can read this file top to bottom and know exactly what the system is
capable of checking, regardless of which domain config points at it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from compliance_tracker.config_schema import RuleConfig


@dataclass
class RuleResult:
    rule_id: str
    field: str | None
    severity: str
    value: str
    message: str


def _check_required(value: str, params: dict[str, Any], record: dict[str, str]) -> bool:
    return bool(value and value.strip())


def _check_not_expired(value: str, params: dict[str, Any], record: dict[str, str]) -> bool:
    if not value or not value.strip():
        return False
    try:
        expiry = date.fromisoformat(value.strip())
    except ValueError:
        return False
    max_age_days = params["max_age_days"]
    days_since_expiry = (date.today() - expiry).days
    return days_since_expiry <= max_age_days


def _check_min_value(value: str, params: dict[str, Any], record: dict[str, str]) -> bool:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return False
    return number >= params["min"]


def _check_max_value(value: str, params: dict[str, Any], record: dict[str, str]) -> bool:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return False
    return number <= params["max"]


def _check_allowed_values(value: str, params: dict[str, Any], record: dict[str, str]) -> bool:
    return value in params["values"]


def _check_regex_match(value: str, params: dict[str, Any], record: dict[str, str]) -> bool:
    if not value:
        return False
    return re.match(params["pattern"], value) is not None


def _resolve_expected_filename(params: dict[str, Any], record: dict[str, str]) -> str:
    return params["filename_pattern"].format(**record)


def _check_document_on_file(value: str, params: dict[str, Any], record: dict[str, str]) -> bool:
    expected_name = _resolve_expected_filename(params, record)
    return (Path(params["directory"]) / expected_name).exists()


RULE_CHECKS: dict[str, Callable[[str, dict[str, Any], dict[str, str]], bool]] = {
    "required": _check_required,
    "not_expired": _check_not_expired,
    "min_value": _check_min_value,
    "max_value": _check_max_value,
    "allowed_values": _check_allowed_values,
    "regex_match": _check_regex_match,
    "document_on_file": _check_document_on_file,
}


def resolve_value(rule: RuleConfig, record: dict[str, str]) -> str:
    """The value a rule checks for a given record -- the raw field value for
    field-based rules, or the resolved expected filename for document_on_file.
    Exposed so callers (e.g. the tracker sheet) can display what a rule
    checked regardless of whether it passed or failed."""
    if rule.type == "document_on_file":
        return _resolve_expected_filename(rule.params, record)
    return record.get(rule.field, "")


def evaluate_rule(rule: RuleConfig, record: dict[str, str]) -> RuleResult | None:
    """Return a RuleResult if the record fails the rule, None if it passes."""
    check_fn = RULE_CHECKS[rule.type]
    value = resolve_value(rule, record)

    if check_fn(value, rule.params, record):
        return None

    # record is spread first so the freshly-read `value` always wins if a
    # column happens to be named "value" -- avoids format() rejecting a
    # duplicate keyword.
    message = rule.message.format(**{**record, "value": value})
    return RuleResult(
        rule_id=rule.id,
        field=rule.field,
        severity=rule.severity,
        value=value,
        message=message,
    )
