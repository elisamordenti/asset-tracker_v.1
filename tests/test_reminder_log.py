from datetime import date

from compliance_tracker.reminder_log import append_entries, load_log, load_summary


def test_load_log_returns_empty_list_when_file_absent(tmp_path):
    assert load_log(tmp_path / "missing.csv") == []


def test_append_entries_creates_log_with_header(tmp_path):
    log_path = tmp_path / "reminder_log.csv"
    append_entries(log_path, ["AST-1", "AST-2"], run_date=date(2026, 8, 1))

    rows = load_log(log_path)
    assert len(rows) == 2
    assert {r["asset_id"] for r in rows} == {"AST-1", "AST-2"}
    assert all(r["reminder_number"] == "1" for r in rows)
    assert all(r["date"] == "2026-08-01" for r in rows)


def test_append_entries_increments_reminder_number_per_asset(tmp_path):
    log_path = tmp_path / "reminder_log.csv"
    append_entries(log_path, ["AST-1"], run_date=date(2026, 8, 1))
    append_entries(log_path, ["AST-1"], run_date=date(2026, 8, 8))
    append_entries(log_path, ["AST-1"], run_date=date(2026, 8, 15))

    summary = load_summary(log_path)
    assert summary["AST-1"].count == 3
    assert summary["AST-1"].last_sent == "2026-08-15"


def test_append_entries_tracks_assets_independently(tmp_path):
    log_path = tmp_path / "reminder_log.csv"
    append_entries(log_path, ["AST-1", "AST-2"], run_date=date(2026, 8, 1))
    append_entries(log_path, ["AST-1"], run_date=date(2026, 8, 8))

    summary = load_summary(log_path)
    assert summary["AST-1"].count == 2
    assert summary["AST-2"].count == 1


def test_load_summary_omits_assets_never_reminded(tmp_path):
    log_path = tmp_path / "reminder_log.csv"
    append_entries(log_path, ["AST-1"], run_date=date(2026, 8, 1))

    summary = load_summary(log_path)
    assert "AST-2" not in summary
