"""Command-line entry point: run the full pipeline for one domain config.

Usage:
    python -m compliance_tracker run --config config/energy_assets.yaml

Paths inside a config (source.path) are resolved relative to the current
working directory, so run this from the repo root.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from compliance_tracker.config_schema import ConfigError, load_config
from compliance_tracker.email_drafter import draft_emails
from compliance_tracker.excel_report import generate_excel_report
from compliance_tracker.validator import validate_assets


def _slugify(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_")


def run(config_path: str, output_dir: str | None = None) -> int:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    try:
        results = validate_assets(config)
    except (FileNotFoundError, ValueError) as e:
        print(f"Data error: {e}", file=sys.stderr)
        return 1

    domain_slug = _slugify(config.domain)
    base_output = Path(output_dir) if output_dir else Path("output") / domain_slug

    excel_path = generate_excel_report(config, results, base_output / "tracker.xlsx")
    drafts = draft_emails(config, results, base_output / "emails")

    flagged = [r for r in results if r.violations]
    print(f"Domain: {config.domain}")
    print(f"Assets loaded: {len(results)}")
    print(f"Flagged: {len(flagged)} ({len(results) - len(flagged)} compliant)")
    print(f"Excel tracker: {excel_path}")
    print(f"Email drafts: {len(drafts)} written to {base_output / 'emails'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compliance_tracker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Validate assets and generate the tracker + email drafts")
    run_parser.add_argument("--config", required=True, help="Path to a domain YAML config")
    run_parser.add_argument("--output-dir", default=None, help="Override the output directory (default: output/<domain>)")

    args = parser.parse_args(argv)

    if args.command == "run":
        return run(args.config, args.output_dir)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
