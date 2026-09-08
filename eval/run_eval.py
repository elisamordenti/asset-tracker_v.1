"""Runs the real extraction pipeline against eval/fixtures/ and measures it.

This is the actual "evaluate AI" step: not a single accuracy number, but a
verdict per case that weights failures by what they'd cost downstream --

  MATCH           -- either correctly auto-filed, or correctly held back
                     because the document is genuinely ambiguous (two
                     assets in one file, wrong domain, no asset reference
                     at all) and no automation was possible anyway.
  FALSE_NEGATIVE   -- wrong, but confident enough to have been silently
                     trusted (auto-filed). The dangerous failure: a bad
                     value reaches the compliance tracker looking like a
                     good one.
  FALSE_POSITIVE   -- a real answer existed but got held back for human
                     review anyway. Costs someone ~30 seconds, nothing more.

Each case is also checked for two things independent of the verdict above:
  - citation verification: does the model's own cited source text actually
    appear in the document? A citation that doesn't verify is a concrete,
    mechanical hallucination signal -- it needs no ground truth at all.
  - router behavior: did the deterministic pre-check
    (extraction.try_deterministic_match) resolve the case without an LLM
    call? A router false positive (resolving a genuinely ambiguous case
    deterministically) would be worse than a slow LLM call, since it means
    skipping the safety net on a hard case -- this should never happen and
    is flagged loudly in the report if it does.

Makes no Claude API calls unless ANTHROPIC_API_KEY is set (same opt-in-only
pattern as email_drafter.send_drafted_emails) -- importing or dry-running
this module never costs anything.

    python eval/run_eval.py
"""

from __future__ import annotations

import csv
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from compliance_tracker.config_schema import load_config  # noqa: E402
from compliance_tracker.extraction import (  # noqa: E402
    build_document_type_candidates,
    extract_content,
    extract_document,
)
from compliance_tracker.intake import CONFIDENT_LEVELS  # noqa: E402
from compliance_tracker.loaders import build_loader  # noqa: E402

CONFIG_PATH = REPO_ROOT / "config" / "energy_assets.yaml"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
GROUND_TRUTH_PATH = Path(__file__).parent / "ground_truth.csv"
RESULTS_PATH = Path(__file__).parent / "results.md"


@dataclass
class CaseResult:
    fixture_file: str
    failure_mode: str
    expected_asset_id: str | None
    expected_document_type: str | None
    expected_resolution: str
    field: str | None
    expected_value: str | None
    actual_asset_id: str | None
    actual_document_type: str | None
    actual_value: str | None
    actual_method: str
    confidence: str
    verdict: str
    citation_status: str  # "verified" | "unverified" | "n/a"


def _load_ground_truth() -> list[dict[str, str]]:
    with GROUND_TRUTH_PATH.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _norm(value: str | None) -> str | None:
    return value if value else None


def evaluate_case(row: dict[str, str], candidates, known_asset_ids, client) -> CaseResult:
    expected_asset_id = _norm(row["expected_asset_id"])
    expected_document_type = _norm(row["expected_document_type"])
    field = _norm(row["field"])
    expected_value = _norm(row["expected_value"])
    expected_resolution = row["expected_resolution"]

    fixture_path = FIXTURES_DIR / row["fixture_file"]
    content = extract_content(fixture_path)
    result = extract_document(row["fixture_file"], content, known_asset_ids, candidates, client=client)

    actual_value = result.fields.get(field) if field else None
    would_be_filed = (
        result.confidence in CONFIDENT_LEVELS and result.asset_id is not None
        and result.document_type_rule_id is not None
    )

    correct = (
        result.asset_id == expected_asset_id
        and result.document_type_rule_id == expected_document_type
        and (field is None or actual_value == expected_value)
    )
    ground_truth_is_resolvable = expected_asset_id is not None and expected_document_type is not None

    if would_be_filed:
        # Auto-filed either way -- what matters is whether it was actually right.
        verdict = "MATCH" if correct else "FALSE_NEGATIVE"
    elif ground_truth_is_resolvable:
        # Held back even though a real answer existed -- costs a human ~30s,
        # regardless of whether the low-confidence guess happened to be right.
        verdict = "FALSE_POSITIVE"
    else:
        # Genuinely ambiguous input (two assets, wrong domain, no asset
        # reference), correctly not auto-filed -- no automation was possible
        # here anyway, so this is the pipeline behaving correctly.
        verdict = "MATCH"

    if result.method == "deterministic":
        citation_status = "n/a"
    elif field is None or actual_value is None:
        citation_status = "n/a"
    else:
        citation = result.citations.get(field, "")
        citation_status = "verified" if citation and citation in content.text else "unverified"

    return CaseResult(
        fixture_file=row["fixture_file"],
        failure_mode=row["failure_mode"],
        expected_asset_id=expected_asset_id,
        expected_document_type=expected_document_type,
        expected_resolution=expected_resolution,
        field=field,
        expected_value=expected_value,
        actual_asset_id=result.asset_id,
        actual_document_type=result.document_type_rule_id,
        actual_value=actual_value,
        actual_method=result.method,
        confidence=result.confidence,
        verdict=verdict,
        citation_status=citation_status,
    )


def _write_results(cases: list[CaseResult]) -> None:
    counts = Counter(c.verdict for c in cases)
    total = len(cases)
    citation_checked = [c for c in cases if c.citation_status != "n/a"]
    citation_verified = sum(1 for c in citation_checked if c.citation_status == "verified")
    deterministic = sum(1 for c in cases if c.actual_method == "deterministic")
    router_false_positives = [
        c for c in cases if c.actual_method == "deterministic" and c.expected_resolution == "llm"
    ]

    lines = []
    lines.append("# Extraction pipeline: eval results\n")
    lines.append(f"Ran {total} fixtures from `eval/fixtures/` against `config/energy_assets.yaml`.\n")

    lines.append("## Verdicts\n")
    for verdict in ["MATCH", "FALSE_NEGATIVE", "FALSE_POSITIVE"]:
        lines.append(f"- **{verdict}**: {counts.get(verdict, 0)} / {total}")
    lines.append("")
    lines.append(
        "FALSE_NEGATIVE is the dangerous bucket: a wrong value that would have been "
        "silently trusted (auto-filed). FALSE_POSITIVE costs a human ~30 seconds of "
        "review for a document that was actually resolvable. A genuinely ambiguous "
        "document (two assets in one file, wrong domain, no asset reference at all) "
        "that gets correctly held for review counts as MATCH -- no automation was "
        "possible there regardless of pipeline quality, so declining to guess is the "
        "right outcome, not a cost."
    )
    lines.append("")

    lines.append("## Router (deterministic pre-check)\n")
    lines.append(f"- Resolved without any LLM call: {deterministic} / {total}")
    lines.append(f"- Router false positives (resolved deterministically but ground truth expected llm): "
                 f"{len(router_false_positives)}")
    if router_false_positives:
        lines.append("  - " + ", ".join(c.fixture_file for c in router_false_positives))
    lines.append("")

    lines.append("## Citation verification (hallucination check)\n")
    if citation_checked:
        lines.append(f"- Verified: {citation_verified} / {len(citation_checked)}")
    else:
        lines.append("- No LLM-path fields to check (nothing has been run live yet).")
    lines.append("")

    lines.append("## Error taxonomy (by failure_mode)\n")
    lines.append("| fixture | failure_mode | verdict | expected | actual | citation |")
    lines.append("|---|---|---|---|---|---|")
    for c in cases:
        expected = f"asset={c.expected_asset_id or 'null'}, value={c.expected_value or 'null'}"
        actual = f"asset={c.actual_asset_id or 'null'}, value={c.actual_value or 'null'}, method={c.actual_method}"
        lines.append(f"| {c.fixture_file} | {c.failure_mode} | {c.verdict} | {expected} | {actual} | {c.citation_status} |")
    lines.append("")

    RESULTS_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set -- refusing to run. This script makes real "
            "Claude API calls for every ambiguous fixture (the ones the deterministic "
            "router can't resolve on its own). Set ANTHROPIC_API_KEY and re-run when "
            "you're ready to spend that.",
            file=sys.stderr,
        )
        return 1

    config = load_config(CONFIG_PATH)
    candidates = build_document_type_candidates(config)
    known_records = build_loader(config.source).load()
    known_asset_ids = [r[config.source.id_field] for r in known_records]

    rows = _load_ground_truth()
    cases = [evaluate_case(row, candidates, known_asset_ids, client=None) for row in rows]
    _write_results(cases)

    print(f"Wrote {RESULTS_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
