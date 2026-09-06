from compliance_tracker.config_schema import load_config


def write_config(tmp_path, csv_path, extra_yaml=""):
    csv_path.write_text("asset_id,contact_name,contact_email\nAST-1,Dana,dana@example.com\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
domain: "Test Domain"
{extra_yaml}
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
    return config_path


def test_registry_key_defaults_to_domain_when_omitted(tmp_path):
    config_path = write_config(tmp_path, tmp_path / "assets.csv")
    config = load_config(config_path)

    assert config.registry_key == "Test Domain"


def test_registry_key_respects_explicit_value(tmp_path):
    config_path = write_config(tmp_path, tmp_path / "assets.csv", extra_yaml="registry_key: shared_pool")
    config = load_config(config_path)

    assert config.domain == "Test Domain"
    assert config.registry_key == "shared_pool"


def test_two_configs_can_share_a_registry_key_while_differing_in_domain(tmp_path):
    csv_path = tmp_path / "assets.csv"
    config_a = write_config(tmp_path, csv_path, extra_yaml="registry_key: shared_pool")

    config_path_b = tmp_path / "config_b.yaml"
    config_path_b.write_text(
        f"""
domain: "Different Display Name"
registry_key: shared_pool
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

    a = load_config(config_a)
    b = load_config(config_path_b)

    assert a.domain != b.domain
    assert a.registry_key == b.registry_key == "shared_pool"
