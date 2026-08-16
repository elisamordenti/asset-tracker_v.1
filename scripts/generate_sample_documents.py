"""One-off generator for the synthetic PDF documents under data/*_documents/.

Writes small, genuinely valid single-page PDFs (hand-built PDF structure, no
extra dependency) so the repo's sample document archive looks and opens like
real filed paperwork. Run this to regenerate the archive from scratch; the
output is committed like the CSV sample data, so this script normally doesn't
need to be re-run.

    python scripts/generate_sample_documents.py
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_minimal_pdf(lines: list[str]) -> bytes:
    """A minimal, valid single-page PDF containing the given lines of text."""
    content_lines = []
    y = 720
    for line in lines:
        content_lines.append(f"BT /F1 12 Tf 72 {y} Td ({_escape(line)}) Tj ET")
        y -= 20
    content = "\n".join(content_lines).encode("latin-1")

    stream_obj = b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream"

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        stream_obj,
    ]

    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"

    xref_offset = len(buf)
    buf += f"xref\n0 {len(objects) + 1}\n".encode()
    buf += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += b"trailer\n"
    buf += f"<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    buf += b"startxref\n"
    buf += f"{xref_offset}\n".encode()
    buf += b"%%EOF"
    return bytes(buf)


def write_doc(directory: Path, filename: str, title: str, asset_id: str, extra: str = "") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        title,
        f"Asset / Entity: {asset_id}",
        "",
        "This is a synthetic document generated for a portfolio project demo.",
        "No real client, asset, or company data is contained in this file.",
    ]
    if extra:
        lines.append(extra)
    (directory / filename).write_bytes(build_minimal_pdf(lines))


# (asset_id, doc_type, present) -- present=False means the file is deliberately
# NOT generated, so the document_on_file rule flags it as missing.
ENERGY_DOCS = [
    ("insurance_certificate", "Insurance Certificate"),
    ("grid_connection_certificate", "Grid Connection Certificate"),
    ("permit", "Operating Permit"),
]

ENERGY_MISSING = {
    ("AST-006", "permit"),
    ("AST-016", "permit"),
    ("AST-003", "insurance_certificate"),
    ("AST-011", "insurance_certificate"),
    ("AST-002", "grid_connection_certificate"),
    ("AST-013", "grid_connection_certificate"),
}

ENERGY_ASSET_IDS = [f"AST-{i:03d}" for i in range(1, 19)]

PORTFOLIO_DOCS = [
    ("audited_financials_report", "Audited Financial Statements"),
    ("cap_table_export", "Capitalization Table Export"),
]

PORTFOLIO_MISSING = {
    ("PC-005", "audited_financials_report"),
    ("PC-010", "audited_financials_report"),
    ("PC-004", "cap_table_export"),
    ("PC-013", "cap_table_export"),
}

PORTFOLIO_COMPANY_IDS = [f"PC-{i:03d}" for i in range(1, 16)]


def main() -> None:
    energy_dir = REPO_ROOT / "data" / "energy_assets_documents"
    for asset_id in ENERGY_ASSET_IDS:
        for doc_type, title in ENERGY_DOCS:
            if (asset_id, doc_type) in ENERGY_MISSING:
                continue
            write_doc(energy_dir, f"{asset_id}_{doc_type}.pdf", title, asset_id)

    portfolio_dir = REPO_ROOT / "data" / "portfolio_companies_documents"
    for company_id in PORTFOLIO_COMPANY_IDS:
        for doc_type, title in PORTFOLIO_DOCS:
            if (company_id, doc_type) in PORTFOLIO_MISSING:
                continue
            write_doc(portfolio_dir, f"{company_id}_{doc_type}.pdf", title, company_id)

    print(f"Wrote energy asset documents to {energy_dir}")
    print(f"Wrote portfolio company documents to {portfolio_dir}")


if __name__ == "__main__":
    main()
