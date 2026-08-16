"""Persistent overlay of values the intake pipeline extracted from documents.

The base CSV registry stays the human/system-of-record layer. Extraction
never edits it directly -- instead, every extracted value is appended here
with its source and confidence, and the most recent value per (asset_id,
field) is layered on top of the base record at load time. This keeps a
clean, auditable separation between "a human entered this" and "a document
said this," and never silently mutates a registry that might be synced from
elsewhere.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path


def append_values(
    path: str | Path,
    asset_id: str,
    fields: dict[str, str],
    source_file: str,
    confidence: str,
    run_date: date | None = None,
) -> None:
    if not fields:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    run_date = run_date or date.today()

    is_new_file = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if is_new_file:
            writer.writerow(["date", "asset_id", "field", "value", "source_file", "confidence"])
        for field_name, value in fields.items():
            writer.writerow([run_date.isoformat(), asset_id, field_name, value, source_file, confidence])


def load_latest_values(path: str | Path) -> dict[str, dict[str, str]]:
    """{asset_id: {field: value}}, most recent row per (asset_id, field)."""
    path = Path(path)
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    latest: dict[str, dict[str, str]] = {}
    seen_dates: dict[tuple[str, str], str] = {}
    for row in rows:
        key = (row["asset_id"], row["field"])
        if key not in seen_dates or row["date"] >= seen_dates[key]:
            seen_dates[key] = row["date"]
            latest.setdefault(row["asset_id"], {})[row["field"]] = row["value"]
    return latest


def apply_to_records(records: list[dict[str, str]], path: str | Path, id_field: str) -> list[dict[str, str]]:
    """Overlay extracted values on top of loaded base records. Extraction
    never invents new asset rows -- only fields on assets already present."""
    latest = load_latest_values(path)
    merged = []
    for record in records:
        overlay = latest.get(record.get(id_field, ""), {})
        merged.append({**record, **overlay})
    return merged
