"""Pure-pandas, no streamlit dependency -- infer_filter_specs/apply_filters
are exactly what app.py's Streamlit widgets are built and applied from."""

import pandas as pd

from compliance_tracker.filters import FilterSpec, apply_filters, apply_search, infer_filter_specs


def test_infer_filter_specs_low_cardinality_column_is_categorical():
    df = pd.DataFrame({"Status": ["FLAGGED", "COMPLIANT", "FLAGGED"]})
    specs = infer_filter_specs(df, ["Status"])

    assert specs == [FilterSpec("Status", "categorical", options=["COMPLIANT", "FLAGGED"])]


def test_infer_filter_specs_numeric_column_is_numeric_range():
    df = pd.DataFrame({"Margin %": ["2", "10", "5"]})
    specs = infer_filter_specs(df, ["Margin %"])

    assert specs[0].kind == "numeric_range"
    assert specs[0].min_value == 2
    assert specs[0].max_value == 10


def test_infer_filter_specs_date_column_is_date_range():
    df = pd.DataFrame({"Insurance Expiry": ["2026-01-01", "2027-06-15"]})
    specs = infer_filter_specs(df, ["Insurance Expiry"])

    assert specs[0].kind == "date_range"


def test_infer_filter_specs_high_cardinality_text_column_is_text():
    values = [f"Free-text note number {i}" for i in range(20)]
    df = pd.DataFrame({"Notes": values})
    specs = infer_filter_specs(df, ["Notes"])

    assert specs[0].kind == "text"


def test_infer_filter_specs_skips_column_missing_from_dataframe():
    df = pd.DataFrame({"A": ["1"]})
    assert infer_filter_specs(df, ["B"]) == []


def test_infer_filter_specs_skips_all_empty_column():
    df = pd.DataFrame({"Notes": ["", ""]})
    assert infer_filter_specs(df, ["Notes"]) == []


def test_apply_filters_categorical_narrows_rows():
    df = pd.DataFrame({"Status": ["FLAGGED", "COMPLIANT"], "ID": ["AST-1", "AST-2"]})
    specs = [FilterSpec("Status", "categorical", options=["COMPLIANT", "FLAGGED"])]

    filtered = apply_filters(df, specs, {"Status": ["FLAGGED"]})

    assert filtered["ID"].tolist() == ["AST-1"]


def test_apply_filters_text_search_is_case_insensitive_substring():
    df = pd.DataFrame({"Location": ["Berlin Site", "Paris Site"], "ID": ["AST-1", "AST-2"]})
    specs = [FilterSpec("Location", "text")]

    filtered = apply_filters(df, specs, {"Location": "berlin"})

    assert filtered["ID"].tolist() == ["AST-1"]


def test_apply_filters_numeric_range_narrows_rows():
    df = pd.DataFrame({"Margin %": ["2", "10", "5"], "ID": ["AST-1", "AST-2", "AST-3"]})
    specs = [FilterSpec("Margin %", "numeric_range", min_value=2, max_value=10)]

    filtered = apply_filters(df, specs, {"Margin %": (4, 10)})

    assert filtered["ID"].tolist() == ["AST-2", "AST-3"]


def test_apply_filters_empty_selection_leaves_column_unfiltered():
    df = pd.DataFrame({"Status": ["FLAGGED", "COMPLIANT"], "ID": ["AST-1", "AST-2"]})
    specs = [FilterSpec("Status", "categorical", options=["COMPLIANT", "FLAGGED"])]

    filtered = apply_filters(df, specs, {"Status": []})

    assert filtered["ID"].tolist() == ["AST-1", "AST-2"]


def test_apply_search_matches_across_any_column_case_insensitively():
    df = pd.DataFrame({
        "Asset ID": ["AST-1", "AST-2"],
        "Location": ["Aberdeen, UK", "Riverside, CA, USA"],
    })

    filtered = apply_search(df, "aberdeen")

    assert filtered["Asset ID"].tolist() == ["AST-1"]


def test_apply_search_matches_on_any_column_not_just_the_first():
    df = pd.DataFrame({
        "Asset ID": ["AST-1", "AST-2"],
        "Notes / Follow-up": ["", "waiting on renewal"],
    })

    filtered = apply_search(df, "renewal")

    assert filtered["Asset ID"].tolist() == ["AST-2"]


def test_apply_search_empty_query_returns_dataframe_unchanged():
    df = pd.DataFrame({"Asset ID": ["AST-1", "AST-2"]})

    filtered = apply_search(df, "")

    assert filtered["Asset ID"].tolist() == ["AST-1", "AST-2"]
