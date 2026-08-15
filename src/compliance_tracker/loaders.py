"""Asset record loaders. CSV is the only implementation today; the abstract
base class is the swap point for future sources (Airtable, a REST API, a
database) without changing the validator, reporter, or email drafter."""

from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from pathlib import Path

from compliance_tracker.config_schema import SourceConfig


class AssetLoader(ABC):
    @abstractmethod
    def load(self) -> list[dict[str, str]]:
        """Return one dict per asset, field name -> raw string value."""


class CSVLoader(AssetLoader):
    def __init__(self, source: SourceConfig):
        self.source = source

    def load(self) -> list[dict[str, str]]:
        path = Path(self.source.path)
        if not path.exists():
            raise FileNotFoundError(f"Asset data source not found: {path}")

        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows = [dict(row) for row in reader]

        id_field = self.source.id_field
        seen_ids: set[str] = set()
        for i, row in enumerate(rows):
            if id_field not in row:
                raise ValueError(
                    f"Row {i} is missing id field '{id_field}' "
                    f"(check source.id_field in the config against the CSV header)"
                )
            asset_id = row[id_field]
            if not asset_id:
                raise ValueError(f"Row {i} has an empty '{id_field}'")
            if asset_id in seen_ids:
                raise ValueError(f"Duplicate asset id '{asset_id}' in {path}")
            seen_ids.add(asset_id)

        return rows


def build_loader(source: SourceConfig) -> AssetLoader:
    if source.type == "csv":
        return CSVLoader(source)
    raise ValueError(f"Unsupported source type: {source.type}")
