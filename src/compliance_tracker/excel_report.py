"""Generates the centralized Excel compliance tracker.

Every column in the Summary and Asset Detail sheets comes from
config.excel.summary_columns / detail_columns -- this module never hardcodes
a field name. Pointing the whole pipeline at a new domain only requires a new
config; this file does not change.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from compliance_tracker.config_schema import AppConfig, ColumnConfig
from compliance_tracker.validator import COMPLIANT, FLAGGED, AssetResult

HEADER_FILL = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")
HEADER_FONT = Font(color="FFFFFF", bold=True)
COMPLIANT_FILL = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
FLAGGED_FILL = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
CRITICAL_FILL = PatternFill(start_color="FCA5A5", end_color="FCA5A5", fill_type="solid")
WARNING_FILL = PatternFill(start_color="FDE68A", end_color="FDE68A", fill_type="solid")

ISSUES_LOG_COLUMNS = [
    ColumnConfig(field="asset_id", label="Asset ID"),
    ColumnConfig(field="rule_id", label="Rule ID"),
    ColumnConfig(field="severity", label="Severity"),
    ColumnConfig(field="field", label="Field"),
    ColumnConfig(field="value", label="Value"),
    ColumnConfig(field="message", label="Message"),
]


def _write_header(ws: Worksheet, columns: list[ColumnConfig]) -> None:
    for col_idx, column in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=col_idx, value=column.label)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.freeze_panes = "A2"


def _autosize_columns(ws: Worksheet, columns: list[ColumnConfig]) -> None:
    for col_idx, column in enumerate(columns, start=1):
        max_len = len(column.label)
        for row in ws.iter_rows(min_col=col_idx, max_col=col_idx, min_row=2):
            value = row[0].value
            if value is not None:
                max_len = max(max_len, len(str(value)))
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = min(max_len + 2, 60)


def _write_summary_sheet(ws: Worksheet, config: AppConfig, results: list[AssetResult]) -> None:
    columns = config.excel.summary_columns
    _write_header(ws, columns)

    ordered = sorted(results, key=lambda r: (r.compliance_status != FLAGGED, r.asset_id))
    status_col_idx = next(
        (i for i, c in enumerate(columns, start=1) if c.field == "compliance_status"), None
    )

    for row_idx, result in enumerate(ordered, start=2):
        fields = result.display_fields()
        for col_idx, column in enumerate(columns, start=1):
            ws.cell(row=row_idx, column=col_idx, value=fields.get(column.field, ""))
        if status_col_idx:
            fill = FLAGGED_FILL if result.compliance_status == FLAGGED else COMPLIANT_FILL
            ws.cell(row=row_idx, column=status_col_idx).fill = fill

    _autosize_columns(ws, columns)


def _write_detail_sheet(ws: Worksheet, config: AppConfig, results: list[AssetResult]) -> None:
    columns = config.excel.detail_columns
    _write_header(ws, columns)

    ordered = sorted(results, key=lambda r: r.asset_id)
    for row_idx, result in enumerate(ordered, start=2):
        fields = result.display_fields()
        for col_idx, column in enumerate(columns, start=1):
            ws.cell(row=row_idx, column=col_idx, value=fields.get(column.field, ""))

    _autosize_columns(ws, columns)


def _write_issues_sheet(ws: Worksheet, results: list[AssetResult]) -> None:
    columns = ISSUES_LOG_COLUMNS
    _write_header(ws, columns)

    ordered = sorted(
        results, key=lambda r: r.asset_id
    )

    row_idx = 2
    for result in ordered:
        for violation in sorted(result.violations, key=lambda v: v.severity != "critical"):
            row_data = {
                "asset_id": result.asset_id,
                "rule_id": violation.rule_id,
                "severity": violation.severity,
                "field": violation.field,
                "value": violation.value,
                "message": violation.message,
            }
            for col_idx, column in enumerate(columns, start=1):
                ws.cell(row=row_idx, column=col_idx, value=row_data.get(column.field, ""))
            severity_col_idx = next(
                (i for i, c in enumerate(columns, start=1) if c.field == "severity"), None
            )
            if severity_col_idx:
                fill = CRITICAL_FILL if violation.severity == "critical" else WARNING_FILL
                ws.cell(row=row_idx, column=severity_col_idx).fill = fill
            row_idx += 1

    _autosize_columns(ws, columns)


def generate_excel_report(config: AppConfig, results: list[AssetResult], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    workbook = Workbook()

    ws_summary = workbook.active
    ws_summary.title = "Summary"
    _write_summary_sheet(ws_summary, config, results)

    ws_detail = workbook.create_sheet("Asset Detail")
    _write_detail_sheet(ws_detail, config, results)

    ws_issues = workbook.create_sheet("Issues Log")
    _write_issues_sheet(ws_issues, results)

    workbook.save(output_path)
    return output_path
