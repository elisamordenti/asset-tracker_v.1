"""LLM-based document classification and field extraction.

This is strictly an extraction front-end: it turns a raw incoming document
into structured field values and hands off to the exact same deterministic
rule engine, unchanged. The LLM never decides compliance -- only what a
document says. Extracted values are shape-checked before being trusted (see
`_validate_field_shape`); a value that doesn't parse as its declared type is
dropped rather than silently written.

Requires the `llm` extra (`pip install -e ".[llm]"`) for the real client;
`classify_and_extract` itself only depends on an injected client object; the
real `anthropic` import happens lazily so tests never need it installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from datetime import date
from pathlib import Path
from typing import Any, Literal, Protocol

from compliance_tracker.config_schema import AppConfig, RuleConfig

DEFAULT_MODEL = "claude-opus-5"


@dataclass
class DocumentTypeCandidate:
    rule_id: str
    label: str
    directory: str
    filename_pattern: str
    extractable_fields: list[dict[str, str]] = dataclass_field(default_factory=list)


def build_document_type_candidates(config: AppConfig) -> list[DocumentTypeCandidate]:
    """One candidate per document_on_file rule in the config. extractable_fields
    is whatever the rule's own YAML declares (an additive, optional key --
    rules with none are presence-only checks, e.g. a permit with no logged
    value)."""
    candidates = []
    for rule in config.rules:
        if rule.type != "document_on_file":
            continue
        candidates.append(
            DocumentTypeCandidate(
                rule_id=rule.id,
                label=rule.label,
                directory=rule.params["directory"],
                filename_pattern=rule.params["filename_pattern"],
                extractable_fields=rule.params.get("extractable_fields", []),
            )
        )
    return candidates


def extract_text(path: str | Path) -> str:
    """Deterministic text extraction -- no AI involved. Dispatches by file
    extension so the same classify_and_extract pipeline can read either a
    PDF or an Excel technical schedule, since real incoming documents arrive
    as both."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_text_from_pdf(path)
    if suffix in (".xlsx", ".xls"):
        return _extract_text_from_excel(path)
    raise ValueError(f"Unsupported document type: {suffix or '(no extension)'}")


def _extract_text_from_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_text_from_excel(path: Path) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(str(path), data_only=True)
    lines = []
    for sheet in workbook.worksheets:
        lines.append(f"[Sheet: {sheet.title}]")
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None]
            if cells:
                lines.append(" | ".join(cells))
    return "\n".join(lines)


@dataclass
class ExtractionResult:
    asset_id: str | None
    document_type_rule_id: str | None
    confidence: Literal["high", "medium", "low"]
    fields: dict[str, str]
    notes: str = ""


def _validate_field_shape(field_name: str, value: str, candidates: list[DocumentTypeCandidate]) -> bool:
    """Shape-check an extracted value against its declared type before it's
    trusted -- don't blindly write whatever the model returned."""
    declared_type = None
    for candidate in candidates:
        for f in candidate.extractable_fields:
            if f["field"] == field_name:
                declared_type = f.get("type")
                break
    if declared_type == "date":
        try:
            date.fromisoformat(value.strip())
        except (ValueError, AttributeError):
            return False
    elif declared_type == "number":
        try:
            float(value)
        except (ValueError, TypeError):
            return False
    return True


class LLMClient(Protocol):
    def parse_extraction(self, system: str, text: str) -> "_RawExtraction": ...


@dataclass
class _RawExtraction:
    asset_id: str | None
    document_type: str | None
    confidence: str
    fields: list[dict[str, str]]


def _build_system_prompt(known_asset_ids: list[str], candidates: list[DocumentTypeCandidate]) -> str:
    doc_type_lines = []
    for c in candidates:
        field_desc = ", ".join(
            f"{f['field']} ({f.get('type', 'text')})" for f in c.extractable_fields
        ) or "none -- presence only, no fields to extract"
        doc_type_lines.append(f"- id: {c.rule_id!r}, label: {c.label!r}, extractable fields: {field_desc}")

    return (
        "You classify a single incoming document and extract structured fields from it.\n\n"
        "Known asset/entity IDs (the document must reference exactly one of these, "
        "or asset_id must be null if you cannot confidently tell which one):\n"
        f"{', '.join(known_asset_ids)}\n\n"
        "Known document types (choose the single best match by id, or null if none fit):\n"
        + "\n".join(doc_type_lines)
        + "\n\n"
        "Extract only the fields declared for the matched document type. Format dates as "
        "ISO YYYY-MM-DD and numbers as plain digits (no currency symbols, no thousands "
        "separators). Set confidence to 'high' only if both the asset and document type "
        "are unambiguous; use 'low' if you are guessing."
    )


def classify_and_extract(
    text: str,
    known_asset_ids: list[str],
    candidates: list[DocumentTypeCandidate],
    client: LLMClient | None = None,
    model: str = DEFAULT_MODEL,
) -> ExtractionResult:
    if client is None:
        client = _AnthropicClient(model=model)

    system = _build_system_prompt(known_asset_ids, candidates)
    raw = client.parse_extraction(system, text)

    valid_fields = {}
    for f in raw.fields:
        if _validate_field_shape(f["field"], f["value"], candidates):
            valid_fields[f["field"]] = f["value"]

    asset_id = raw.asset_id if raw.asset_id in known_asset_ids else None
    doc_type = raw.document_type if raw.document_type in {c.rule_id for c in candidates} else None

    confidence = raw.confidence
    if asset_id is None or doc_type is None:
        confidence = "low"

    return ExtractionResult(
        asset_id=asset_id,
        document_type_rule_id=doc_type,
        confidence=confidence,
        fields=valid_fields,
    )


class _AnthropicClient:
    """Real LLMClient, backed by the Claude API. Only imports `anthropic`
    when actually constructed, so classify_and_extract's default path never
    requires it to be installed unless you actually call it for real."""

    def __init__(self, model: str = DEFAULT_MODEL):
        import anthropic
        from pydantic import BaseModel

        class _ExtractedFieldSchema(BaseModel):
            field: str
            value: str

        class _ExtractionSchema(BaseModel):
            asset_id: str | None = None
            document_type: str | None = None
            confidence: Literal["high", "medium", "low"] = "low"
            fields: list[_ExtractedFieldSchema] = []

        self._schema = _ExtractionSchema
        self._client = anthropic.Anthropic()
        self._model = model

    def parse_extraction(self, system: str, text: str) -> _RawExtraction:
        response = self._client.messages.parse(
            model=self._model,
            max_tokens=1024,
            output_config={"effort": "low"},
            system=system,
            messages=[{"role": "user", "content": text[:20000]}],
            output_format=self._schema,
        )
        parsed = response.parsed_output
        return _RawExtraction(
            asset_id=parsed.asset_id,
            document_type=parsed.document_type,
            confidence=parsed.confidence,
            fields=[{"field": f.field, "value": f.value} for f in parsed.fields],
        )
