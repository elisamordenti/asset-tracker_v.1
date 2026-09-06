"""Lightweight, config-free dynamic filters for the Streamlit tracker table.

No new YAML schema: a filter widget is inferred per displayed column purely
from the values already in the built table -- numeric-looking columns get a
range slider, low-cardinality columns get a dropdown, date-looking columns
get a date range, everything else gets free-text search. Pure pandas, no
streamlit import, so this is directly unit-testable without a running app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import pandas as pd

CATEGORICAL_MAX_UNIQUE = 12

FilterKind = Literal["categorical", "text", "numeric_range", "date_range"]


@dataclass
class FilterSpec:
    column: str
    kind: FilterKind
    options: list[str] | None = None
    min_value: Any | None = None
    max_value: Any | None = None


def _non_empty(series: pd.Series) -> pd.Series:
    return series[series.str.strip() != ""]


def _is_numeric(series: pd.Series) -> bool:
    non_empty = _non_empty(series)
    if non_empty.empty:
        return False
    return pd.to_numeric(non_empty, errors="coerce").notna().all()


def _is_date(series: pd.Series) -> bool:
    non_empty = _non_empty(series)
    if non_empty.empty:
        return False
    parsed = pd.to_datetime(non_empty, errors="coerce", format="ISO8601")
    return parsed.notna().all()


def infer_filter_specs(df: pd.DataFrame, columns: list[str]) -> list[FilterSpec]:
    """One FilterSpec per column that actually exists in `df` and has at
    least one non-empty value."""
    specs = []
    for column in columns:
        if column not in df.columns:
            continue
        series = df[column].astype(str)
        non_empty = _non_empty(series)
        if non_empty.empty:
            continue

        if _is_numeric(series):
            numeric = pd.to_numeric(non_empty, errors="coerce")
            specs.append(FilterSpec(column, "numeric_range", min_value=numeric.min(), max_value=numeric.max()))
        elif _is_date(series):
            parsed = pd.to_datetime(non_empty, errors="coerce", format="ISO8601")
            specs.append(FilterSpec(column, "date_range", min_value=parsed.min().date(), max_value=parsed.max().date()))
        else:
            uniques = sorted(non_empty.unique())
            if len(uniques) <= CATEGORICAL_MAX_UNIQUE:
                specs.append(FilterSpec(column, "categorical", options=uniques))
            else:
                specs.append(FilterSpec(column, "text"))
    return specs


def apply_filters(df: pd.DataFrame, specs: list[FilterSpec], values: dict[str, Any]) -> pd.DataFrame:
    """values: {column: selection} -- a list[str] for categorical, a str for
    text, a (min, max) tuple for the range kinds. A falsy/missing selection
    leaves that column unfiltered."""
    for spec in specs:
        value = values.get(spec.column)
        if not value:
            continue
        series = df[spec.column].astype(str)

        if spec.kind == "categorical":
            df = df[series.isin(value)]
        elif spec.kind == "text":
            df = df[series.str.contains(str(value), case=False, na=False)]
        elif spec.kind == "numeric_range":
            numeric = pd.to_numeric(series, errors="coerce")
            low, high = value
            df = df[numeric.between(low, high)]
        elif spec.kind == "date_range":
            parsed = pd.to_datetime(series, errors="coerce", format="ISO8601")
            low, high = value
            df = df[parsed.dt.date.between(low, high)]
    return df
