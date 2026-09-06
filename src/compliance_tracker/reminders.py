"""One reusable reminder-evaluation-and-send cycle, shared by the Streamlit
button, and scripts/send_reminders.py.

The automated-email trigger mechanism (a daily cron, a GitHub Actions
schedule, Windows Task Scheduler, a long-running server process -- whatever
you end up hosting this on) is deliberately not decided here. Instead,
run_reminder_cycle() is the one place that logic lives, so whichever
scheduler gets picked later only has to invoke scripts/send_reminders.py --
no further refactoring of this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from pathlib import Path

from compliance_tracker.archive import DocumentArchive
from compliance_tracker.config_schema import AppConfig
from compliance_tracker.database import DBClient, append_reminders, load_reminder_summary, load_results_and_notes
from compliance_tracker.email_drafter import DraftedEmail, draft_emails, send_drafted_emails


@dataclass
class ReminderCycleResult:
    flagged_count: int
    drafts: list[DraftedEmail] = dataclass_field(default_factory=list)
    sent: int | None = None  # None unless send=True was requested
    send_error: str | None = None


def run_reminder_cycle(
    config: AppConfig,
    db: DBClient,
    output_dir: str | Path,
    send: bool = False,
    archive: DocumentArchive | None = None,
) -> ReminderCycleResult:
    """Evaluate every asset under config.registry_key, draft one email per
    flagged asset (the trigger: the asset currently fails one or more
    configured rules -- e.g. a missing/expired document), and optionally
    send them. Safe to call repeatedly -- drafting always records a new
    reminder in the persistent log first, so each draft's follow-up
    numbering is correct."""
    results, _ = load_results_and_notes(db, config, archive=archive)
    flagged = [r for r in results if r.violations]

    append_reminders(db, config.registry_key, [r.asset_id for r in flagged])
    reminder_summary = load_reminder_summary(db, config.registry_key)
    drafts = draft_emails(config, results, reminder_summary, Path(output_dir) / "emails")

    sent, send_error = None, None
    if send:
        try:
            sent = send_drafted_emails(drafts)
        except RuntimeError as e:
            send_error = str(e)

    return ReminderCycleResult(flagged_count=len(flagged), drafts=drafts, sent=sent, send_error=send_error)
