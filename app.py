"""Asset Compliance Tracker -- primary interface for day-to-day use.

Run with:
    pip install -e ".[webapp,llm]"
    streamlit run app.py

Requires SUPABASE_URL and SUPABASE_KEY (service-role key) as environment
variables -- see database.py's module docstring for the schema to run in
Supabase's SQL editor first, and archive.py's docstring for the Storage
bucket a document upload gets filed into (name it via
SUPABASE_STORAGE_BUCKET, default "documents"). Optional: ANTHROPIC_API_KEY
for the document upload/classify feature (PDF, Excel, or a photo/scan --
images go straight to Claude's vision input, no OCR step); EMAIL_SEND_MODE=live
+ SMTP_* to actually send reminder emails instead of just drafting them.

This file is UI glue only -- every piece of actual logic it calls into
(database.py, validator.py, excel_report.py, email_drafter.py,
extraction.py, intake.py, reminders.py, filters.py) is independently
unit-tested; this page is verified by running it, not by an automated test.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from compliance_tracker.archive import build_supabase_storage_archive
from compliance_tracker.config_schema import load_config
from compliance_tracker.database import (
    build_supabase_client,
    load_reminder_summary,
    load_results_and_notes,
    record_extraction_outcome,
    save_note,
    sync_registry,
)
from compliance_tracker.excel_report import build_tracker_table, generate_excel_report
from compliance_tracker.extraction import build_document_type_candidates
from compliance_tracker.filters import apply_filters, infer_filter_specs
from compliance_tracker.intake import process_upload
from compliance_tracker.reminders import run_reminder_cycle

load_dotenv()  # picks up a local .env file, if present, before any os.environ.get() below

st.set_page_config(page_title="Asset Compliance Tracker", layout="wide")

CONFIG_DIR = Path("config")


@st.cache_resource
def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        st.error("Set SUPABASE_URL and SUPABASE_KEY environment variables before running this app.")
        st.stop()
    return build_supabase_client(url, key)


@st.cache_resource
def get_archive():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        st.error("Set SUPABASE_URL and SUPABASE_KEY environment variables before running this app.")
        st.stop()
    bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", "documents")
    return build_supabase_storage_archive(url, key, bucket)


def decorate_rule_columns(config, ordered, rows) -> list[list]:
    """Prefix each rule cell with a pass/fail mark. st.data_editor can't
    combine per-cell background styling with inline editing, so the
    checklist signal has to live in the text itself instead of a fill
    color (unlike the Excel/Sheets paths)."""
    n_info = len(config.excel.info_columns)
    decorated = [list(row) for row in rows]
    for i, result in enumerate(ordered):
        violated_ids = {v.rule_id for v in result.violations}
        for j, rule in enumerate(config.rules):
            col_idx = n_info + j
            mark = "✗ " if rule.id in violated_ids else "✓ "
            decorated[i][col_idx] = f"{mark}{decorated[i][col_idx]}"
    return decorated


def render_filter_widgets(specs) -> dict:
    """Streamlit-touching half of the dynamic filter feature (see
    filters.py for the pure inference/apply logic). One widget per inferred
    column, kept in a collapsed sidebar section so a wide portfolio's info
    columns don't crowd out the existing Status filter."""
    values = {}
    with st.sidebar.expander(f"Filters ({len(specs)} field(s))"):
        for spec in specs:
            if spec.kind == "categorical":
                values[spec.column] = st.multiselect(spec.column, spec.options, key=f"filter_{spec.column}")
            elif spec.kind == "text":
                values[spec.column] = st.text_input(spec.column, key=f"filter_{spec.column}")
            elif spec.kind == "numeric_range":
                if spec.min_value == spec.max_value:
                    values[spec.column] = None
                else:
                    values[spec.column] = st.slider(
                        spec.column, float(spec.min_value), float(spec.max_value),
                        (float(spec.min_value), float(spec.max_value)), key=f"filter_{spec.column}",
                    )
            elif spec.kind == "date_range":
                if spec.min_value == spec.max_value:
                    values[spec.column] = None
                else:
                    picked = st.date_input(
                        spec.column, (spec.min_value, spec.max_value), key=f"filter_{spec.column}",
                    )
                    # date_input returns a single date, not a (low, high) pair,
                    # until the user has picked both ends of the range.
                    values[spec.column] = picked if isinstance(picked, tuple) and len(picked) == 2 else None
    return values


def main():
    st.title("Asset Compliance Tracker")

    config_files = sorted(CONFIG_DIR.glob("*.yaml"))
    if not config_files:
        st.error(f"No config files found in {CONFIG_DIR}/")
        st.stop()

    configs = {}
    config_paths = {}
    for path in config_files:
        config = load_config(path)
        configs[config.domain] = config
        config_paths[config.domain] = path

    domain_name = st.sidebar.selectbox("Domain", list(configs.keys()))
    config = configs[domain_name]
    base_output_dir = Path("output") / config.registry_key

    client = get_client()
    archive = get_archive()

    if st.sidebar.button("Sync from registry"):
        n = sync_registry(client, config)
        st.sidebar.success(f"Synced {n} asset(s) from the registry.")
        st.rerun()

    results, notes_by_asset = load_results_and_notes(client, config, archive=archive)
    if not results:
        st.info("No assets loaded yet -- click 'Sync from registry' in the sidebar.")
        st.stop()

    reminder_summary = load_reminder_summary(client, config.registry_key)
    flagged = [r for r in results if r.violations]

    col1, col2, col3 = st.columns(3)
    col1.metric("Assets", len(results))
    col2.metric("Flagged", len(flagged))
    col3.metric("Compliant", len(results) - len(flagged))

    headers, ordered, rows = build_tracker_table(config, results, reminder_summary, notes_by_asset)
    decorated_rows = decorate_rule_columns(config, ordered, rows)
    df = pd.DataFrame(decorated_rows, columns=headers)

    status_label = "Status"
    show = st.sidebar.radio("Show", ["All", "Flagged", "Compliant"], horizontal=True)
    if show == "Flagged":
        df = df[df[status_label] == "FLAGGED"]
    elif show == "Compliant":
        df = df[df[status_label] == "COMPLIANT"]

    notes_labels = [c.label for c in config.excel.notes_columns]
    id_label = headers[0]

    filterable_columns = [c.label for c in config.excel.info_columns[1:]] + notes_labels
    filter_specs = infer_filter_specs(df, filterable_columns)
    filter_values = render_filter_widgets(filter_specs)
    df = apply_filters(df, filter_specs, filter_values)

    column_config = {h: st.column_config.Column(disabled=True) for h in headers if h not in notes_labels}

    st.subheader("Tracker")
    edited_df = st.data_editor(
        df, column_config=column_config, hide_index=True, use_container_width=True, key="tracker_editor"
    )

    if st.button("Save notes"):
        changed = 0
        for i in range(len(df)):
            asset_id = str(edited_df.iloc[i][id_label])
            current_notes = dict(notes_by_asset.get(asset_id, {}))
            row_changed = False
            for label in notes_labels:
                new_val = edited_df.iloc[i][label]
                if new_val != current_notes.get(label, ""):
                    current_notes[label] = new_val
                    row_changed = True
            if row_changed:
                save_note(client, config.registry_key, asset_id, current_notes)
                changed += 1
        st.success(f"Saved {changed} note edit(s).")
        st.rerun()

    st.divider()
    st.subheader("Reminders")
    st.write(f"{len(flagged)} of {len(results)} assets are flagged.")
    if st.button("Draft reminders for flagged assets"):
        outcome = run_reminder_cycle(
            config, client, base_output_dir,
            send=os.environ.get("EMAIL_SEND_MODE") == "live",
            archive=archive,
        )
        st.success(f"Drafted {len(outcome.drafts)} email(s) to {base_output_dir / 'emails'}")
        if outcome.sent is not None:
            st.success(f"Sent {outcome.sent} email(s).")
        elif outcome.send_error:
            st.error(outcome.send_error)
        st.rerun()
    st.caption(
        "This only runs when someone clicks the button above. To send reminders on a "
        f"schedule instead, run `python scripts/send_reminders.py --config {config_paths[domain_name]} "
        "--send` from whatever scheduler you end up hosting this on (cron, a GitHub Action, Task Scheduler, ...)."
    )

    st.divider()
    st.subheader("Export")
    if st.button("Export to Excel"):
        path = generate_excel_report(config, results, reminder_summary, base_output_dir / "tracker.xlsx")
        st.success(f"Exported to {path}")

    st.divider()
    st.subheader("Upload a document")
    uploaded = st.file_uploader(
        "Drop a PDF, Excel file, or a photo/scan (any filename -- it gets classified automatically)",
        type=["pdf", "xlsx", "xls", "jpg", "jpeg", "png"],
    )
    if uploaded is not None and st.button("Classify & file"):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            st.error("Set ANTHROPIC_API_KEY to use document classification.")
        else:
            content = uploaded.getvalue()
            candidates = build_document_type_candidates(config)
            known_ids = [r.asset_id for r in results]

            def record_values(asset_id, fields, source_file, confidence):
                extracted_at = date.today().isoformat()
                for field_name, value in fields.items():
                    client.append_extracted_value(
                        config.registry_key, asset_id, field_name, value, source_file, confidence, extracted_at
                    )

            outcome = process_upload(uploaded.name, content, archive, known_ids, candidates, record_values)

            if outcome.outcome == "filed":
                sync_registry(client, config)
                st.success(f"Filed as {outcome.target_path.name}")
            else:
                archive.upload("_pending_review", uploaded.name, content)
                st.warning("Couldn't confidently classify this document -- filed under _pending_review for manual review.")

            record_extraction_outcome(client, config.registry_key, outcome)
            st.rerun()


if __name__ == "__main__":
    main()
