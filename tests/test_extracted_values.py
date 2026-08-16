from datetime import date

from compliance_tracker.extracted_values import append_values, apply_to_records, load_latest_values


def test_load_latest_values_returns_empty_when_file_absent(tmp_path):
    assert load_latest_values(tmp_path / "missing.csv") == {}


def test_append_and_load_round_trip(tmp_path):
    path = tmp_path / "extracted_values.csv"
    append_values(path, "AST-1", {"insurance_expiry": "2027-01-01"}, "doc.pdf", "high", run_date=date(2026, 8, 1))

    latest = load_latest_values(path)
    assert latest == {"AST-1": {"insurance_expiry": "2027-01-01"}}


def test_append_values_with_empty_fields_is_noop(tmp_path):
    path = tmp_path / "extracted_values.csv"
    append_values(path, "AST-1", {}, "doc.pdf", "high")
    assert not path.exists()


def test_most_recent_value_wins_per_field(tmp_path):
    path = tmp_path / "extracted_values.csv"
    append_values(path, "AST-1", {"insurance_expiry": "2026-06-01"}, "old.pdf", "high", run_date=date(2026, 6, 1))
    append_values(path, "AST-1", {"insurance_expiry": "2027-03-01"}, "new.pdf", "high", run_date=date(2026, 8, 1))

    latest = load_latest_values(path)
    assert latest["AST-1"]["insurance_expiry"] == "2027-03-01"


def test_apply_to_records_overlays_extracted_values(tmp_path):
    path = tmp_path / "extracted_values.csv"
    append_values(path, "AST-1", {"insurance_expiry": "2027-03-01"}, "doc.pdf", "high")

    records = [{"asset_id": "AST-1", "insurance_expiry": "2020-01-01", "margin_pct": "10"}]
    merged = apply_to_records(records, path, "asset_id")

    assert merged[0]["insurance_expiry"] == "2027-03-01"
    assert merged[0]["margin_pct"] == "10"  # untouched fields survive


def test_apply_to_records_never_invents_new_assets(tmp_path):
    path = tmp_path / "extracted_values.csv"
    append_values(path, "AST-999", {"insurance_expiry": "2027-03-01"}, "doc.pdf", "high")

    records = [{"asset_id": "AST-1", "insurance_expiry": "2020-01-01"}]
    merged = apply_to_records(records, path, "asset_id")

    assert len(merged) == 1
    assert merged[0]["insurance_expiry"] == "2020-01-01"  # AST-999's data never applied
