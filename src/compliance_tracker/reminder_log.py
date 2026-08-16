"""Persistent, append-only record of when each asset was last reminded.

Compliance chasing is recurring, not one-shot: a "last reminder sent" column
is only meaningful if the tool remembers its own past runs. This module reads
and writes a small CSV log (one row per reminder ever issued) so that running
the CLI repeatedly builds real history instead of resetting on every run.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class ReminderSummary:
    last_sent: str | None
    count: int


def load_log(path: str | Path) -> list[dict[str, str]]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def summarize(log_rows: list[dict[str, str]]) -> dict[str, ReminderSummary]:
    """One ReminderSummary per asset_id: most recent date sent + total count."""
    summary: dict[str, ReminderSummary] = {}
    for row in log_rows:
        asset_id = row["asset_id"]
        existing = summary.get(asset_id)
        count = int(row["reminder_number"])
        if existing is None or row["date"] > existing.last_sent:
            summary[asset_id] = ReminderSummary(last_sent=row["date"], count=count)
        else:
            summary[asset_id] = ReminderSummary(last_sent=existing.last_sent, count=max(existing.count, count))
    return summary


def append_entries(path: str | Path, asset_ids: list[str], run_date: date | None = None) -> None:
    """Append one new reminder entry per asset_id, numbered from that asset's
    prior count + 1. Creates the log (with header) if it doesn't exist yet."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_date = run_date or date.today()

    existing_rows = load_log(path)
    summary = summarize(existing_rows)

    is_new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if is_new_file:
            writer.writerow(["date", "asset_id", "reminder_number"])
        for asset_id in asset_ids:
            next_number = summary.get(asset_id, ReminderSummary(None, 0)).count + 1
            writer.writerow([run_date.isoformat(), asset_id, next_number])
            summary[asset_id] = ReminderSummary(last_sent=run_date.isoformat(), count=next_number)


def load_summary(path: str | Path) -> dict[str, ReminderSummary]:
    return summarize(load_log(path))
