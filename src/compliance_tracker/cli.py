"""Command-line entry point: run the full pipeline for one domain config.

Usage:
    python -m compliance_tracker run --config config/energy_assets.yaml

Paths inside a config (source.path) are resolved relative to the current
working directory, so run this from the repo root.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from compliance_tracker.config_schema import AppConfig, ConfigError, load_config
from compliance_tracker.email_drafter import draft_emails, send_drafted_emails
from compliance_tracker.excel_report import generate_excel_report
from compliance_tracker.reminder_log import ReminderSummary, append_entries, load_summary
from compliance_tracker.validator import AssetResult, validate_assets


def _slugify(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_")


def _load_and_validate(config_path: str) -> tuple[AppConfig, list[AssetResult]] | int:
    """Returns (config, results) on success, or an exit code on failure."""
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

    return config, results


def _update_reminder_log(base_output: Path, results: list[AssetResult]) -> tuple[Path, dict[str, ReminderSummary]]:
    flagged = [r for r in results if r.violations]
    log_path = base_output / "reminder_log.csv"
    append_entries(log_path, [r.asset_id for r in flagged])
    return log_path, load_summary(log_path)


def run(config_path: str, output_dir: str | None = None) -> int:
    loaded = _load_and_validate(config_path)
    if isinstance(loaded, int):
        return loaded
    config, results = loaded

    domain_slug = _slugify(config.domain)
    base_output = Path(output_dir) if output_dir else Path("output") / domain_slug

    flagged = [r for r in results if r.violations]
    log_path, reminder_summary = _update_reminder_log(base_output, results)

    excel_path = generate_excel_report(config, results, reminder_summary, base_output / "tracker.xlsx")
    drafts = draft_emails(config, results, reminder_summary, base_output / "emails")

    print(f"Domain: {config.domain}")
    print(f"Assets loaded: {len(results)}")
    print(f"Flagged: {len(flagged)} ({len(results) - len(flagged)} compliant)")
    print(f"Excel tracker: {excel_path}")
    print(f"Email drafts: {len(drafts)} written to {base_output / 'emails'}")
    print(f"Reminder log: {log_path}")
    return 0


def sync(config_path: str, sheet_id: str, worksheet: str, send: bool, output_dir: str | None = None) -> int:
    loaded = _load_and_validate(config_path)
    if isinstance(loaded, int):
        return loaded
    config, results = loaded

    credentials_path = os.environ.get("GOOGLE_SHEETS_CREDENTIALS_PATH")
    if not credentials_path:
        print(
            "Sync error: GOOGLE_SHEETS_CREDENTIALS_PATH is not set. "
            "Create a Google Cloud service account, download its JSON key, "
            "share the target Sheet with the service account's email address, "
            "and point this env var at the key file. See the README for the full setup.",
            file=sys.stderr,
        )
        return 1

    try:
        from compliance_tracker.sheets_sync import build_gspread_client, sync_tracker
    except ImportError:
        print(
            "Sync error: the 'sheets' extra isn't installed. "
            'Run: pip install -e ".[sheets]"',
            file=sys.stderr,
        )
        return 1

    domain_slug = _slugify(config.domain)
    base_output = Path(output_dir) if output_dir else Path("output") / domain_slug

    flagged = [r for r in results if r.violations]
    log_path, reminder_summary = _update_reminder_log(base_output, results)

    client = build_gspread_client(sheet_id, worksheet, credentials_path)
    sync_tracker(client, config, results, reminder_summary)

    drafts = draft_emails(config, results, reminder_summary, base_output / "emails")

    print(f"Domain: {config.domain}")
    print(f"Assets loaded: {len(results)}")
    print(f"Flagged: {len(flagged)} ({len(results) - len(flagged)} compliant)")
    print(f"Synced Tracker to Google Sheet {sheet_id} (worksheet '{worksheet}')")
    print(f"Email drafts: {len(drafts)} written to {base_output / 'emails'}")
    print(f"Reminder log: {log_path}")

    if send:
        try:
            sent = send_drafted_emails(drafts)
            print(f"Sent {sent} email(s)")
        except RuntimeError as e:
            print(f"Send error: {e}", file=sys.stderr)
            return 1
    else:
        print("Emails drafted but not sent (pass --send, plus EMAIL_SEND_MODE=live + SMTP_* env vars, to actually send)")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="compliance_tracker")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Validate assets and generate the tracker + email drafts")
    run_parser.add_argument("--config", required=True, help="Path to a domain YAML config")
    run_parser.add_argument("--output-dir", default=None, help="Override the output directory (default: output/<domain>)")

    sync_parser = subparsers.add_parser(
        "sync", help="Validate assets, sync the Tracker to a live Google Sheet, and optionally send emails"
    )
    sync_parser.add_argument("--config", required=True, help="Path to a domain YAML config")
    sync_parser.add_argument("--sheet-id", required=True, help="Google Sheet ID to sync the Tracker into")
    sync_parser.add_argument("--worksheet", default="Tracker", help="Worksheet/tab name within the sheet")
    sync_parser.add_argument(
        "--send", action="store_true",
        help="Actually send reminder emails (still requires EMAIL_SEND_MODE=live + SMTP_* env vars)",
    )
    sync_parser.add_argument("--output-dir", default=None, help="Override the output directory (default: output/<domain>)")

    args = parser.parse_args(argv)

    if args.command == "run":
        return run(args.config, args.output_dir)
    if args.command == "sync":
        return sync(args.config, args.sheet_id, args.worksheet, args.send, args.output_dir)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
