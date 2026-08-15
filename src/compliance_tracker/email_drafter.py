"""Drafts follow-up emails for flagged assets, addressed to whoever the
config says is the responsible contact.

Draft-to-file is the only thing that happens by default. Real sending is an
explicit, separate opt-in gated entirely by environment variables -- never a
default, never a hardcoded credential. See send_drafted_emails().
"""

from __future__ import annotations

import os
import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from compliance_tracker.config_schema import AppConfig
from compliance_tracker.validator import AssetResult

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass
class DraftedEmail:
    asset_id: str
    to_name: str
    to_email: str
    subject: str
    body: str
    file_path: Path


def _safe_filename(asset_id: str) -> str:
    return _SAFE_FILENAME_RE.sub("_", asset_id)


def _render(template: str, context: dict[str, str]) -> str:
    return template.format(**context)


def draft_emails(
    config: AppConfig, results: list[AssetResult], output_dir: str | Path
) -> list[DraftedEmail]:
    """Write one .txt draft per flagged asset. Never sends anything."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    drafts = []
    for result in results:
        if not result.violations:
            continue

        fields = result.display_fields()
        contact_name = fields.get(config.contact.name_field, "")
        contact_email = fields.get(config.contact.email_field, "")

        issues_list = "\n".join(
            f"  - [{v.severity.upper()}] {v.message}" for v in result.violations
        )
        context = {**fields, "contact_name": contact_name, "issues_list": issues_list}

        subject = _render(config.email.subject_template, context)
        body = _render(config.email.body_template, context)

        file_path = output_dir / f"{_safe_filename(result.asset_id)}.txt"
        file_path.write_text(
            f"To: {contact_name} <{contact_email}>\nSubject: {subject}\n\n{body}\n",
            encoding="utf-8",
        )

        drafts.append(
            DraftedEmail(
                asset_id=result.asset_id,
                to_name=contact_name,
                to_email=contact_email,
                subject=subject,
                body=body,
                file_path=file_path,
            )
        )

    return drafts


def send_drafted_emails(drafts: list[DraftedEmail]) -> int:
    """Send drafted emails via SMTP. Only does anything if EMAIL_SEND_MODE=live
    and all SMTP_* env vars are set -- otherwise raises, so callers can't send
    by accident. Returns the number of emails sent."""
    if os.environ.get("EMAIL_SEND_MODE") != "live":
        raise RuntimeError(
            "Refusing to send: EMAIL_SEND_MODE is not 'live'. "
            "Drafts remain in the output directory as .txt files."
        )

    required_env = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"]
    missing = [name for name in required_env if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"EMAIL_SEND_MODE=live but missing env var(s): {', '.join(missing)}"
        )

    host = os.environ["SMTP_HOST"]
    port = int(os.environ["SMTP_PORT"])
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASSWORD"]

    sent = 0
    with smtplib.SMTP(host, port) as smtp:
        smtp.starttls()
        smtp.login(user, password)
        for draft in drafts:
            if not draft.to_email:
                continue
            msg = EmailMessage()
            msg["From"] = user
            msg["To"] = draft.to_email
            msg["Subject"] = draft.subject
            msg.set_content(draft.body)
            smtp.send_message(msg)
            sent += 1

    return sent
