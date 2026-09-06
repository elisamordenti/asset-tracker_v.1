"""Orchestrates loading asset records and evaluating every configured rule
against each one. This is the only module that knows both "loaders" and
"rules" exist -- it has no domain knowledge of its own."""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field

from compliance_tracker.archive import DocumentArchive
from compliance_tracker.config_schema import AppConfig
from compliance_tracker.loaders import build_loader
from compliance_tracker.rules import RuleContext, RuleResult, evaluate_rule

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


def validate_assets(
    config: AppConfig,
    records: list[dict[str, str]] | None = None,
    archive: DocumentArchive | None = None,
) -> list[AssetResult]:
    """If records is omitted, loads fresh from config.source as before. Pass
    records explicitly to validate against a pre-loaded set -- e.g. the
    base registry merged with the intake pipeline's extracted-values
    overlay (see extracted_values.apply_to_records).

    Pass `archive` whenever the config has document_on_file rules, so they
    can be checked -- omitting it means every document_on_file rule fails
    (nothing found), which is only correct for a config with none. Every
    unique directory across all document_on_file rules is listed exactly
    once here, regardless of how many assets are validated, so validating
    many assets never costs one archive round-trip per asset per rule."""
    if records is None:
        loader = build_loader(config.source)
        records = loader.load()

    directories = {rule.params["directory"] for rule in config.rules if rule.type == "document_on_file"}
    archive_index = {d: archive.list_filenames(d) for d in directories} if archive else {}
    context = RuleContext(archive_index=archive_index)

    results = []
    for record in records:
        violations = [
            result
            for rule in config.rules
            if (result := evaluate_rule(rule, record, context)) is not None
        ]
        results.append(
            AssetResult(
                asset_id=record[config.source.id_field],
                record=record,
                violations=violations,
            )
        )
    return results
