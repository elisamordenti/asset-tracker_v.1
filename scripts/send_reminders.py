"""Evaluate flagged assets and draft (optionally send) reminder emails for
one config, against the Supabase-backed registry app.py also reads from.

This script exists so that whichever way you end up scheduling automated
reminders -- a cron job, a GitHub Actions workflow on a schedule, Windows
Task Scheduler, a long-running server process -- has a single, stable
command to invoke. Nothing here decides the scheduler for you; wire it up
however fits wherever this ends up hosted.

Usage:
    python scripts/send_reminders.py --config config/energy_assets.yaml
    python scripts/send_reminders.py --config config/energy_assets.yaml --send

Requires SUPABASE_URL, SUPABASE_KEY (and SUPABASE_STORAGE_BUCKET if it's not
the default "documents"). --send additionally requires EMAIL_SEND_MODE=live
plus SMTP_HOST/PORT/USER/PASSWORD. Install with: pip install -e ".[webapp]"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from compliance_tracker.archive import build_supabase_storage_archive
from compliance_tracker.config_schema import load_config
from compliance_tracker.database import build_supabase_client
from compliance_tracker.reminders import run_reminder_cycle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, help="Path to a domain YAML config")
    parser.add_argument(
        "--send", action="store_true",
        help="Actually send (requires EMAIL_SEND_MODE=live + SMTP_* env vars; otherwise only drafts)",
    )
    parser.add_argument("--output-dir", default=None, help="Override the output directory (default: output/<registry_key>)")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    output_dir = Path(args.output_dir) if args.output_dir else Path("output") / config.registry_key

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        print("SUPABASE_URL and SUPABASE_KEY must be set.", file=sys.stderr)
        return 1

    db = build_supabase_client(url, key)
    bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", "documents")
    archive = build_supabase_storage_archive(url, key, bucket)

    outcome = run_reminder_cycle(config, db, output_dir, send=args.send, archive=archive)

    print(f"Flagged: {outcome.flagged_count}; drafted {len(outcome.drafts)} email(s) to {output_dir / 'emails'}")
    if outcome.sent is not None:
        print(f"Sent {outcome.sent} email(s)")
    elif outcome.send_error:
        print(outcome.send_error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
