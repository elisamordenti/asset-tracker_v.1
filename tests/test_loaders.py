import pytest

from compliance_tracker.config_schema import SourceConfig
from compliance_tracker.loaders import CSVLoader, build_loader


def write_csv(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_csv_loader_reads_rows(tmp_path):
    path = write_csv(
        tmp_path,
        "assets.csv",
        "asset_id,asset_name\nAST-1,Alpha\nAST-2,Beta\n",
    )
    source = SourceConfig(type="csv", path=str(path), id_field="asset_id")
    rows = CSVLoader(source).load()
    assert rows == [
        {"asset_id": "AST-1", "asset_name": "Alpha"},
        {"asset_id": "AST-2", "asset_name": "Beta"},
    ]


def test_csv_loader_raises_on_missing_file(tmp_path):
    source = SourceConfig(type="csv", path=str(tmp_path / "missing.csv"), id_field="asset_id")
    with pytest.raises(FileNotFoundError):
        CSVLoader(source).load()


def test_csv_loader_raises_on_duplicate_id(tmp_path):
    path = write_csv(
        tmp_path,
        "assets.csv",
        "asset_id,asset_name\nAST-1,Alpha\nAST-1,Alpha Duplicate\n",
    )
    source = SourceConfig(type="csv", path=str(path), id_field="asset_id")
    with pytest.raises(ValueError, match="Duplicate asset id"):
        CSVLoader(source).load()


def test_csv_loader_raises_on_empty_id(tmp_path):
    path = write_csv(tmp_path, "assets.csv", "asset_id,asset_name\n,Alpha\n")
    source = SourceConfig(type="csv", path=str(path), id_field="asset_id")
    with pytest.raises(ValueError, match="empty"):
        CSVLoader(source).load()


def test_csv_loader_raises_on_wrong_id_field(tmp_path):
    path = write_csv(tmp_path, "assets.csv", "asset_id,asset_name\nAST-1,Alpha\n")
    source = SourceConfig(type="csv", path=str(path), id_field="company_id")
    with pytest.raises(ValueError, match="missing id field"):
        CSVLoader(source).load()


def test_build_loader_returns_csv_loader_for_csv_type():
    source = SourceConfig(type="csv", path="whatever.csv", id_field="asset_id")
    loader = build_loader(source)
    assert isinstance(loader, CSVLoader)


def test_build_loader_raises_for_unsupported_type():
    source = SourceConfig(type="airtable", path="whatever", id_field="asset_id")
    with pytest.raises(ValueError, match="Unsupported source type"):
        build_loader(source)
