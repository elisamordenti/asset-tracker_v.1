from unittest.mock import MagicMock

from compliance_tracker import cli


def test_sync_requires_credentials_env_var(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_SHEETS_CREDENTIALS_PATH", raising=False)
    csv_path = tmp_path / "assets.csv"
    csv_path.write_text("asset_id,contact_name,contact_email\nAST-1,Dana,dana@example.com\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
domain: "Test"
source: {{type: csv, path: "{csv_path.as_posix()}", id_field: asset_id}}
contact: {{name_field: contact_name, email_field: contact_email}}
rules:
  - {{id: has_id, field: asset_id, type: required, severity: warning, message: "missing"}}
excel:
  info_columns:
    - {{field: asset_id, label: "ID"}}
email:
  subject_template: "s"
  body_template: "b"
""",
        encoding="utf-8",
    )

    exit_code = cli.sync(str(config_path), "fake-sheet-id", "Tracker", send=False, output_dir=str(tmp_path / "output"))

    assert exit_code == 1
    assert "GOOGLE_SHEETS_CREDENTIALS_PATH" in capsys.readouterr().err


def test_sync_calls_sheets_backend_with_expected_args(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_SHEETS_CREDENTIALS_PATH", "/fake/creds.json")

    csv_path = tmp_path / "assets.csv"
    csv_path.write_text("asset_id,contact_name,contact_email\nAST-1,Dana,dana@example.com\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
domain: "Test"
source: {{type: csv, path: "{csv_path.as_posix()}", id_field: asset_id}}
contact: {{name_field: contact_name, email_field: contact_email}}
rules:
  - {{id: has_id, field: asset_id, type: required, severity: warning, message: "missing"}}
excel:
  info_columns:
    - {{field: asset_id, label: "ID"}}
email:
  subject_template: "s"
  body_template: "b"
""",
        encoding="utf-8",
    )

    fake_client = object()
    mock_build_client = MagicMock(return_value=fake_client)
    mock_sync_tracker = MagicMock()

    import compliance_tracker.sheets_sync as sheets_sync_module
    monkeypatch.setattr(sheets_sync_module, "build_gspread_client", mock_build_client)
    monkeypatch.setattr(sheets_sync_module, "sync_tracker", mock_sync_tracker)

    exit_code = cli.sync(
        str(config_path), "my-sheet-id", "MyTab", send=False, output_dir=str(tmp_path / "output")
    )

    assert exit_code == 0
    mock_build_client.assert_called_once_with("my-sheet-id", "MyTab", "/fake/creds.json")
    mock_sync_tracker.assert_called_once()
    assert mock_sync_tracker.call_args[0][0] is fake_client
