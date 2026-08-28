"""Orchestrates the inbox -> archive + extracted-values pipeline.

For every file sitting in a domain's inbox folder: extract its text, ask the
LLM to classify and extract (extraction.classify_and_extract), and either
file it into the document archive + log the extracted values, or -- if the
match isn't confident -- leave it exactly where it is and log it as needing
human review. Never silently guesses.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import date
from pathlib import Path

from compliance_tracker.config_schema import AppConfig
from compliance_tracker.extracted_values import append_values
from compliance_tracker.extraction import (
    LLMClient,
    build_document_type_candidates,
    classify_and_extract,
    extract_text,
)
from compliance_tracker.loaders import build_loader

CONFIDENT_LEVELS = {"high", "medium"}
SUPPORTED_EXTENSIONS = {".pdf", ".xlsx", ".xls"}


@dataclass
class IntakeFileOutcome:
    source_filename: str
    outcome: str  # "filed" | "needs_review"
    asset_id: str | None
    document_type_rule_id: str | None
    confidence: str
    target_path: Path | None = None


@dataclass
class IntakeSummary:
    outcomes: list[IntakeFileOutcome] = dataclass_field(default_factory=list)

    @property
    def filed(self) -> list[IntakeFileOutcome]:
        return [o for o in self.outcomes if o.outcome == "filed"]

    @property
    def needs_review(self) -> list[IntakeFileOutcome]:
        return [o for o in self.outcomes if o.outcome == "needs_review"]

    @property
    def skipped(self) -> list[IntakeFileOutcome]:
        return [o for o in self.outcomes if o.outcome == "skipped_unsupported_type"]


def _log_row(log_path: Path, run_date: date, outcome: IntakeFileOutcome) -> None:
    import csv

    log_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not log_path.exists()
    with log_path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["date", "source_file", "outcome", "asset_id", "document_type", "confidence"])
        writer.writerow([
            run_date.isoformat(),
            outcome.source_filename,
            outcome.outcome,
            outcome.asset_id or "",
            outcome.document_type_rule_id or "",
            outcome.confidence,
        ])


def run_intake(
    config: AppConfig,
    inbox_dir: str | Path,
    extracted_values_path: str | Path,
    log_path: str | Path,
    client: LLMClient | None = None,
) -> IntakeSummary:
    inbox_dir = Path(inbox_dir)
    extracted_values_path = Path(extracted_values_path)
    log_path = Path(log_path)
    run_date = date.today()

    candidates = build_document_type_candidates(config)
    known_records = build_loader(config.source).load()
    known_asset_ids = [r[config.source.id_field] for r in known_records]

    summary = IntakeSummary()
    if not inbox_dir.exists():
        return summary

    for path in sorted(inbox_dir.iterdir()):
        if not path.is_file():
            continue

        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            outcome = IntakeFileOutcome(
                source_filename=path.name, outcome="skipped_unsupported_type",
                asset_id=None, document_type_rule_id=None, confidence="n/a",
            )
            _log_row(log_path, run_date, outcome)
            summary.outcomes.append(outcome)
            continue

        text = extract_text(path)
        result = classify_and_extract(text, known_asset_ids, candidates, client=client)

        if result.confidence in CONFIDENT_LEVELS and result.asset_id and result.document_type_rule_id:
            candidate = next(c for c in candidates if c.rule_id == result.document_type_rule_id)
            target_name = candidate.filename_pattern.format(asset_id=result.asset_id)
            target_path = Path(candidate.directory) / target_name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target_path)

            if result.fields:
                append_values(
                    extracted_values_path,
                    result.asset_id,
                    result.fields,
                    source_file=path.name,
                    confidence=result.confidence,
                    run_date=run_date,
                )

            outcome = IntakeFileOutcome(
                source_filename=path.name,
                outcome="filed",
                asset_id=result.asset_id,
                document_type_rule_id=result.document_type_rule_id,
                confidence=result.confidence,
                target_path=target_path,
            )
        else:
            outcome = IntakeFileOutcome(
                source_filename=path.name,
                outcome="needs_review",
                asset_id=result.asset_id,
                document_type_rule_id=result.document_type_rule_id,
                confidence=result.confidence,
            )

        _log_row(log_path, run_date, outcome)
        summary.outcomes.append(outcome)

    return summary
