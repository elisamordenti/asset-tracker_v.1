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
from typing import Any, Callable

from compliance_tracker.config_schema import RuleConfig


@dataclass
class RuleResult:
    rule_id: str
    field: str
    severity: str
    value: str
    message: str


def _check_required(value: str, params: dict[str, Any]) -> bool:
    return bool(value and value.strip())


def _check_not_expired(value: str, params: dict[str, Any]) -> bool:
    if not value or not value.strip():
        return False
    try:
        expiry = date.fromisoformat(value.strip())
    except ValueError:
        return False
    max_age_days = params["max_age_days"]
    days_since_expiry = (date.today() - expiry).days
    return days_since_expiry <= max_age_days


def _check_min_value(value: str, params: dict[str, Any]) -> bool:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return False
    return number >= params["min"]


def _check_max_value(value: str, params: dict[str, Any]) -> bool:
    try:
        number = float(value)
    except (ValueError, TypeError):
        return False
    return number <= params["max"]


def _check_allowed_values(value: str, params: dict[str, Any]) -> bool:
    return value in params["values"]


def _check_regex_match(value: str, params: dict[str, Any]) -> bool:
    if not value:
        return False
    return re.match(params["pattern"], value) is not None


RULE_CHECKS: dict[str, Callable[[str, dict[str, Any]], bool]] = {
    "required": _check_required,
    "not_expired": _check_not_expired,
    "min_value": _check_min_value,
    "max_value": _check_max_value,
    "allowed_values": _check_allowed_values,
    "regex_match": _check_regex_match,
}


def evaluate_rule(rule: RuleConfig, record: dict[str, str]) -> RuleResult | None:
    """Return a RuleResult if the record fails the rule, None if it passes."""
    value = record.get(rule.field, "")
    check_fn = RULE_CHECKS[rule.type]

    if check_fn(value, rule.params):
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
