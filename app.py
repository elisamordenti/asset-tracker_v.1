"""Asset Compliance Tracker -- primary interface for day-to-day use.

Run with:
    pip install -e ".[webapp]"
    streamlit run app.py

Requires SUPABASE_URL and SUPABASE_KEY (service-role key) as environment
variables -- see database.py's module docstring for the schema to run in
Supabase's SQL editor first. Optional: ANTHROPIC_API_KEY for the document
upload/classify feature; EMAIL_SEND_MODE=live + SMTP_* to actually send
reminder emails instead of just drafting them.

This file is UI glue only -- every piece of actual logic it calls into
(database.py, validator.py, excel_report.py, email_drafter.py,
extraction.py) is independently unit-tested; this page is verified by
running it, not by an automated test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from compliance_tracker.config_schema import load_config
from compliance_tracker.database import (
    append_reminders,
    build_supabase_client,
    load_reminder_summary,
    load_results_and_notes,
    record_extraction_outcome,
    save_note,
    sync_registry,
)
from compliance_tracker.email_drafter import draft_emails, send_drafted_emails
from compliance_tracker.excel_report import build_tracker_table, generate_excel_report
from compliance_tracker.extracted_values import append_values
from compliance_tracker.extraction import build_document_type_candidates, classify_and_extract, extract_text
from compliance_tracker.intake import IntakeFileOutcome

st.set_page_config(page_title="Asset Compliance Tracker", layout="wide")

CONFIG_DIR = Path("config")


def _slugify(text: str) -> str:
    return "".join(c.lower() if c.isalnum() else "_" for c in text).strip("_")


@st.cache_resource
def get_client():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        st.error("Set SUPABASE_URL and SUPABASE_KEY environment variables before running this app.")
        st.stop()
    return build_supabase_client(url, key)


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


def main():
    st.title("Asset Compliance Tracker")

    config_files = sorted(CONFIG_DIR.glob("*.yaml"))
    if not config_files:
        st.error(f"No config files found in {CONFIG_DIR}/")
        st.stop()

    configs = {}
    for path in config_files:
        config = load_config(path)
        configs[config.domain] = config

    domain_name = st.sidebar.selectbox("Domain", list(configs.keys()))
    config = configs[domain_name]
    base_output_dir = Path("output") / _slugify(domain_name)

    client = get_client()

    if st.sidebar.button("Sync from registry"):
        n = sync_registry(client, config, base_output_dir)
        st.sidebar.success(f"Synced {n} asset(s) from the registry.")
        st.rerun()

    results, notes_by_asset = load_results_and_notes(client, config)
    if not results:
        st.info("No assets loaded yet -- click 'Sync from registry' in the sidebar.")
        st.stop()

    reminder_summary = load_reminder_summary(client, domain_name)
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
                save_note(client, domain_name, asset_id, current_notes)
                changed += 1
        st.success(f"Saved {changed} note edit(s).")
        st.rerun()

    st.divider()
    st.subheader("Reminders")
    st.write(f"{len(flagged)} of {len(results)} assets are flagged.")
    if st.button("Draft reminders for flagged assets"):
        append_reminders(client, domain_name, [r.asset_id for r in flagged])
        reminder_summary = load_reminder_summary(client, domain_name)
        drafts = draft_emails(config, results, reminder_summary, base_output_dir / "emails")
        st.success(f"Drafted {len(drafts)} email(s) to {base_output_dir / 'emails'}")
        if os.environ.get("EMAIL_SEND_MODE") == "live":
            try:
                sent = send_drafted_emails(drafts)
                st.success(f"Sent {sent} email(s).")
            except RuntimeError as e:
                st.error(str(e))
        st.rerun()

    st.divider()
    st.subheader("Export")
    if st.button("Export to Excel"):
        path = generate_excel_report(config, results, reminder_summary, base_output_dir / "tracker.xlsx")
        st.success(f"Exported to {path}")

    st.divider()
    st.subheader("Upload a document")
    uploaded = st.file_uploader(
        "Drop a PDF or Excel file (any filename -- it gets classified automatically)",
        type=["pdf", "xlsx", "xls"],
    )
    if uploaded is not None and st.button("Classify & file"):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            st.error("Set ANTHROPIC_API_KEY to use document classification.")
        else:
            tmp_path = base_output_dir / "_uploads" / uploaded.name
            tmp_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.write_bytes(uploaded.getvalue())

            candidates = build_document_type_candidates(config)
            known_ids = [r.asset_id for r in results]
            text = extract_text(tmp_path)
            extraction_result = classify_and_extract(text, known_ids, candidates)

            if extraction_result.confidence in {"high", "medium"} and extraction_result.asset_id and extraction_result.document_type_rule_id:
                candidate = next(c for c in candidates if c.rule_id == extraction_result.document_type_rule_id)
                target_name = candidate.filename_pattern.format(asset_id=extraction_result.asset_id)
                target_path = Path(candidate.directory) / target_name
                target_path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path.rename(target_path)

                if extraction_result.fields:
                    append_values(
                        base_output_dir / "extracted_values.csv",
                        extraction_result.asset_id,
                        extraction_result.fields,
                        source_file=uploaded.name,
                        confidence=extraction_result.confidence,
                    )
                    sync_registry(client, config, base_output_dir)

                outcome = IntakeFileOutcome(
                    source_filename=uploaded.name, outcome="filed",
                    asset_id=extraction_result.asset_id, document_type_rule_id=extraction_result.document_type_rule_id,
                    confidence=extraction_result.confidence, target_path=target_path,
                )
                st.success(f"Filed as {target_path.name}")
            else:
                outcome = IntakeFileOutcome(
                    source_filename=uploaded.name, outcome="needs_review",
                    asset_id=extraction_result.asset_id, document_type_rule_id=extraction_result.document_type_rule_id,
                    confidence=extraction_result.confidence,
                )
                st.warning("Couldn't confidently classify this document -- left for manual review.")

            record_extraction_outcome(client, domain_name, outcome)
            st.rerun()


if __name__ == "__main__":
    main()
