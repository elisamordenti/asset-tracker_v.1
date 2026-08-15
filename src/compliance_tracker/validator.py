"""Orchestrates loading asset records and evaluating every configured rule
against each one. This is the only module that knows both "loaders" and
"rules" exist -- it has no domain knowledge of its own."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field

from compliance_tracker.config_schema import AppConfig
from compliance_tracker.loaders import build_loader
from compliance_tracker.rules import RuleResult, evaluate_rule

COMPLIANT = "COMPLIANT"
FLAGGED = "FLAGGED"


@dataclass
class AssetResult:
    asset_id: str
    record: dict[str, str]
    violations: list[RuleResult] = dataclass_field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "critical")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")

    @property
    def compliance_status(self) -> str:
        return FLAGGED if self.violations else COMPLIANT

    def display_fields(self) -> dict[str, str]:
        """Raw record fields plus the computed fields Excel columns may
        reference (compliance_status, critical_count, warning_count)."""
        return {
            **self.record,
            "compliance_status": self.compliance_status,
            "critical_count": str(self.critical_count),
            "warning_count": str(self.warning_count),
        }


def validate_assets(config: AppConfig) -> list[AssetResult]:
    loader = build_loader(config.source)
    records = loader.load()

    results = []
    for record in records:
        violations = [
            result
            for rule in config.rules
            if (result := evaluate_rule(rule, record)) is not None
        ]
        results.append(
            AssetResult(
                asset_id=record[config.source.id_field],
                record=record,
                violations=violations,
            )
        )
    return results
