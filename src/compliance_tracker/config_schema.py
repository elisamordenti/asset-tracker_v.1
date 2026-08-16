"""Loads and validates the YAML config that fully describes one compliance domain.

Every domain-specific decision (which fields exist, which rules apply, which
columns appear in the Excel tracker, how follow-up emails are worded) lives in
this config. The engine code never branches on domain -- see rules.py for the
fixed vocabulary of rule types that configs are built from.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any

import yaml

SEVERITIES = {"critical", "warning"}

# Each rule type's required extra parameters, beyond id/field/type/severity/message.
# This is the complete, fixed vocabulary of checks the engine understands.
RULE_TYPE_PARAMS: dict[str, list[str]] = {
    "required": [],
    "not_expired": ["max_age_days"],
    "min_value": ["min"],
    "max_value": ["max"],
    "allowed_values": ["values"],
    "regex_match": ["pattern"],
    "document_on_file": ["directory", "filename_pattern"],
}

# Rule types that check a single CSV field. document_on_file instead checks
# the filesystem, resolving its filename pattern from the whole record.
FIELD_BASED_RULE_TYPES = set(RULE_TYPE_PARAMS) - {"document_on_file"}


class ConfigError(Exception):
    """Raised when a config file is missing required fields or malformed."""


@dataclass
class SourceConfig:
    type: str
    path: str
    id_field: str


@dataclass
class ContactConfig:
    name_field: str
    email_field: str


@dataclass
class RuleConfig:
    id: str
    type: str
    severity: str
    message: str
    label: str
    field: str | None = None
    params: dict[str, Any] = dataclass_field(default_factory=dict)


@dataclass
class ColumnConfig:
    field: str
    label: str


@dataclass
class ExcelConfig:
    info_columns: list[ColumnConfig]
    notes_columns: list[ColumnConfig] = dataclass_field(default_factory=list)


@dataclass
class EmailConfig:
    subject_template: str
    body_template: str


@dataclass
class AppConfig:
    domain: str
    source: SourceConfig
    contact: ContactConfig
    rules: list[RuleConfig]
    excel: ExcelConfig
    email: EmailConfig


def _require_keys(d: dict, keys: list[str], context: str) -> None:
    if not isinstance(d, dict):
        raise ConfigError(f"{context} must be a mapping, got {type(d).__name__}")
    missing = [k for k in keys if k not in d]
    if missing:
        raise ConfigError(f"{context} is missing required key(s): {', '.join(missing)}")


def _parse_source(raw: dict) -> SourceConfig:
    _require_keys(raw, ["type", "path", "id_field"], "config.source")
    if raw["type"] != "csv":
        raise ConfigError(
            f"config.source.type '{raw['type']}' is not supported yet "
            "(only 'csv' is implemented; the loader interface is designed to "
            "be extended with new source types without touching validation "
            "or reporting code)"
        )
    return SourceConfig(type=raw["type"], path=raw["path"], id_field=raw["id_field"])


def _parse_contact(raw: dict) -> ContactConfig:
    _require_keys(raw, ["name_field", "email_field"], "config.contact")
    return ContactConfig(name_field=raw["name_field"], email_field=raw["email_field"])


def _humanize(identifier: str) -> str:
    return identifier.replace("_", " ").replace("-", " ").title()


def _parse_rule(raw: dict, index: int) -> RuleConfig:
    context = f"config.rules[{index}]"
    _require_keys(raw, ["id", "type", "severity", "message"], context)

    rule_type = raw["type"]
    if rule_type not in RULE_TYPE_PARAMS:
        known = ", ".join(sorted(RULE_TYPE_PARAMS))
        raise ConfigError(
            f"{context} ('{raw['id']}') has unknown type '{rule_type}'. "
            f"Known types: {known}"
        )

    if rule_type in FIELD_BASED_RULE_TYPES and "field" not in raw:
        raise ConfigError(f"{context} ('{raw['id']}') is missing required key: field")

    severity = raw["severity"]
    if severity not in SEVERITIES:
        raise ConfigError(
            f"{context} ('{raw['id']}') has invalid severity '{severity}'. "
            f"Must be one of: {', '.join(sorted(SEVERITIES))}"
        )

    required_params = RULE_TYPE_PARAMS[rule_type]
    known_top_level = {"id", "field", "type", "severity", "message", "label"}
    params = {k: v for k, v in raw.items() if k not in known_top_level}
    missing_params = [p for p in required_params if p not in params]
    if missing_params:
        raise ConfigError(
            f"{context} ('{raw['id']}', type '{rule_type}') is missing "
            f"required parameter(s): {', '.join(missing_params)}"
        )

    return RuleConfig(
        id=raw["id"],
        field=raw.get("field"),
        type=rule_type,
        severity=severity,
        message=raw["message"],
        label=raw.get("label", _humanize(raw["id"])),
        params=params,
    )


def _parse_columns(raw: list, context: str, allow_empty: bool = False) -> list[ColumnConfig]:
    if not isinstance(raw, list) or (not raw and not allow_empty):
        raise ConfigError(f"{context} must be a non-empty list")
    columns = []
    for i, col in enumerate(raw):
        _require_keys(col, ["field", "label"], f"{context}[{i}]")
        columns.append(ColumnConfig(field=col["field"], label=col["label"]))
    return columns


def _parse_excel(raw: dict) -> ExcelConfig:
    _require_keys(raw, ["info_columns"], "config.excel")
    return ExcelConfig(
        info_columns=_parse_columns(raw["info_columns"], "config.excel.info_columns"),
        notes_columns=_parse_columns(
            raw.get("notes_columns", []), "config.excel.notes_columns", allow_empty=True
        ),
    )


def _parse_email(raw: dict) -> EmailConfig:
    _require_keys(raw, ["subject_template", "body_template"], "config.email")
    return EmailConfig(
        subject_template=raw["subject_template"],
        body_template=raw["body_template"],
    )


def load_config(path: str | Path) -> AppConfig:
    """Load and validate a domain config file, raising ConfigError with a
    specific, actionable message on the first problem found."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    _require_keys(raw, ["domain", "source", "contact", "rules", "excel", "email"], "config")

    if not isinstance(raw["rules"], list) or not raw["rules"]:
        raise ConfigError("config.rules must be a non-empty list")

    seen_ids: set[str] = set()
    rules = []
    for i, rule_raw in enumerate(raw["rules"]):
        rule = _parse_rule(rule_raw, i)
        if rule.id in seen_ids:
            raise ConfigError(f"config.rules contains duplicate id '{rule.id}'")
        seen_ids.add(rule.id)
        rules.append(rule)

    source = _parse_source(raw["source"])
    excel = _parse_excel(raw["excel"])
    if excel.info_columns[0].field != source.id_field:
        raise ConfigError(
            "config.excel.info_columns[0] must be the id field "
            f"('{source.id_field}', matching config.source.id_field) -- it's always "
            "written as the Tracker sheet's first column and used as the merge "
            "key for preserving notes across runs"
        )

    return AppConfig(
        domain=raw["domain"],
        source=source,
        contact=_parse_contact(raw["contact"]),
        rules=rules,
        excel=excel,
        email=_parse_email(raw["email"]),
    )
