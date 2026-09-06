# Asset Compliance Validator

A config-driven engine that turns a folder of raw incoming documents into
structured, validated data: it classifies and extracts values via Claude,
checks hundreds of distributed "assets" against a compliance rulebook, keeps
a living tracker up to date, and drafts — or sends — follow-up emails for
whatever's missing. The primary interface is a small Streamlit web app
(`app.py`, backed by Supabase) where you upload documents, edit notes, and
send reminders directly; local Excel and Google Sheets export remain
available as CLI commands. Zero code changes required to point it at a
completely different domain.

## The problem this replaces

In a consulting role, I worked alongside renewable energy producers managing
hundreds of EU/US solar and wind assets. Regulatory compliance is critical
for bank financing and winning tenders: each asset needs current
documentation, accurate margin and maintenance records, and specific
technical criteria met. Checking compliance and assembling the data room for
banks was a manual, error-prone process that took **about three weeks**, most
of it spent chasing clients for missing documents.

This project rebuilds that workflow as a generic, auditable pipeline:

| | Before | After |
|---|---|---|
| Checking every asset against the rulebook | Manual, spreadsheet by spreadsheet | One command, deterministic |
| Building the bank/tender data room | ~3 weeks | ~1 day |
| Reading a client's PDF and retyping the value | By hand, every document | Claude classifies + extracts; a human only reviews low-confidence cases |
| Knowing *why* something was flagged | Tribal knowledge | Every flag traces to one rule + one value in an Audit Log |
| Confirming a document was actually filed | Digging through email/drive | Checked against a real document archive automatically |
| Tracking who's been chased and when | Sticky notes / memory | A persistent reminder log, visible in the tracker |
| Chasing clients for missing info | Ad hoc emails | Auto-drafted per flagged asset; real sending is one flag away |
| Adapting to a new asset type / schema | Rewrite the spreadsheet macros | Edit a YAML file |

All data in this repo is synthetic. Nothing here comes from a real client.

## How it works

```
Raw inbox/upload      --> intake: Claude classify + extract --> Document archive
(PDF, Excel, photo)                  |                       (local disk, or Supabase Storage)
                                      v                              |
                           extraction log                            v
                         (needs-review trail)          CSV registry (base layer)
                                                                     |
                                                                     v
                                              Loader --> Validator --> Tracker (Excel, a live Google Sheet, or Supabase)
                                                             |               |
                                                       Rules (YAML)    Audit Log (full trail)
                                                             |               |
                                                             v               v
                                                  Reminder Log (persistent)   Email drafts (.txt) --> optionally sent
```

Every domain-specific decision — which fields exist, which rules apply, what
the tracker's columns are called, how the follow-up email reads — lives in
one YAML config per domain. The engine code (`src/compliance_tracker/`)
never mentions a domain by name.

**The validation engine is deterministic and rule-based, not an LLM.** Every
rule is one of seven generic, auditable check types:

| Type | Checks | Example use |
|---|---|---|
| `required` | Field is non-empty | Missing data-room reference |
| `not_expired` | Date field isn't older than today + a grace period | Expired certification |
| `min_value` | Numeric field ≥ a threshold | Runway below minimum |
| `max_value` | Numeric field ≤ a threshold | Downtime over budget |
| `allowed_values` | Field is one of a fixed set | Invalid status value |
| `regex_match` | Field matches a format | Malformed reference code |
| `document_on_file` | A specific file exists in the document archive | Insurance certificate never filed |

A human can read this list and know exactly what the system can and can't
check. That's a deliberate boundary, not an oversight: swapping *which*
fields, thresholds, and documents apply to *any* domain never touches code —
only YAML does. Adding a genuinely new *kind* of check would need one small
function added to [`rules.py`](src/compliance_tracker/rules.py); that's the
one place domain logic could ever leak into code, and it hasn't needed to
for either domain below.

## Quickstart

```bash
pip install -e ".[dev]"

python -m compliance_tracker run --config config/energy_assets.yaml
python -m compliance_tracker run --config config/portfolio_companies.yaml

pytest
```

Each run writes `output/<domain>/tracker.xlsx`, drafted follow-up emails to
`output/<domain>/emails/*.txt`, and a `reminder_log.csv`. `output/` is
gitignored — nothing generated is committed.

## The web app: the primary interface

```bash
pip install -e ".[webapp]"
```

Create a free project at [supabase.com](https://supabase.com) (no card
required), run this in its SQL editor:

```sql
create table assets (
  domain text not null,
  asset_id text not null,
  record jsonb not null,
  notes jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (domain, asset_id)
);

create table reminder_log (
  id bigserial primary key,
  domain text not null,
  asset_id text not null,
  sent_date date not null,
  reminder_number int not null
);

create table extraction_log (
  id bigserial primary key,
  domain text not null,
  source_filename text not null,
  outcome text not null,
  asset_id text,
  document_type text,
  confidence text,
  logged_at timestamptz not null default now()
);

create table extracted_values (
  id bigserial primary key,
  domain text not null,
  asset_id text not null,
  field text not null,
  value text not null,
  source_file text not null,
  confidence text not null,
  extracted_at date not null default current_date
);
```

Then create a **Storage bucket** (Storage → New bucket in the Supabase
dashboard) to hold filed documents — this is where uploads actually live,
since a hosted app has no durable local disk to file them into (see
[`archive.py`](src/compliance_tracker/archive.py)'s module docstring). Name
it whatever you like and set that name as `SUPABASE_STORAGE_BUCKET` (defaults
to `documents` if unset).

Copy [`.env.example`](.env.example) to `.env` and fill in `SUPABASE_URL` and
`SUPABASE_KEY` (the project's service-role key), then run:

```bash
streamlit run app.py
```

This is the intended day-to-day interface: a domain selector, a "Sync from
registry" button that pulls the CSV (plus any values the upload pipeline has
extracted) into the database, a Status filter (All / Flagged / Compliant)
plus dynamic per-column filters inferred from whatever fields the config
displays (a dropdown for a low-cardinality column, a range slider for a
numeric or date column, free-text search otherwise — see
[`filters.py`](src/compliance_tracker/filters.py), no extra config needed) in
the sidebar for triaging a large portfolio, the tracker itself as a live
editable table (each rule column shown as ✓/✗ against its value — Streamlit
can't combine per-cell background color with inline editing, so the
checklist signal lives in the text — notes save straight back to Supabase,
no export/re-import step), a document upload box (PDF, Excel, **or a
photo/scan** — images go straight to Claude's vision input, no separate OCR
step) that runs the same classify/extract pipeline as `intake` (via the
shared `process_upload()` in [`intake.py`](src/compliance_tracker/intake.py)),
a "Draft reminders" button, and an "Export to Excel" button for when a
bank/tender-ready file is still needed.

Filed documents and every value the upload pipeline extracts live in
Supabase (a Storage bucket and the `extracted_values` table respectively) —
nothing about a Supabase-backed run depends on local disk surviving between
requests, so this path is ready to deploy to hosting with no persistent
filesystem whenever you decide where that will be.

Only the `record` column is ever touched by a sync — `notes` is exclusively
yours, same non-destructive-merge principle as the Excel/Sheets notes
column, just backed by a real database instead of reading a file back
before overwriting it. `database.py` is fully unit-tested against a fake
in-memory client (`tests/test_database.py`) — no live Supabase project or
network access needed to run the suite; only to actually use the app.

The CLI paths below (`run` / `sync`) still work exactly as before — the web
app is additive, not a replacement.

## The Tracker: one scannable matrix, not three cross-referenced sheets

The tracker is one row per asset, one column per requirement — the same
shape as the data room checklist this project is meant to replace:

```
$ python -m compliance_tracker run --config config/energy_assets.yaml
Domain: Energy Assets
Assets loaded: 18
Flagged: 14 (4 compliant)
Excel tracker: output\energy_assets\tracker.xlsx
Email drafts: 14 written to output\energy_assets\emails
Reminder log: output\energy_assets\reminder_log.csv
```

Tracker columns for the energy domain (auto-generated from
[`config/energy_assets.yaml`](config/energy_assets.yaml), nothing hardcoded):

```
Asset ID | Asset Name | Location | Responsible Contact
| Grid Cert Expiry | Grid Cert on File | Insurance Expiry | Insurance Cert on File
| Permit on File | Margin % | Downtime (hrs/yr) | Maintenance Status
| Next Deadline | Last Reminder Sent | Reminder Count | Status | Notes / Follow-up
```

Each rule becomes one column: the cell shows the actual value (a date, a
percentage, a filename), color-coded green/red by whether that rule passed —
so the same column is simultaneously a checklist ("is this filed/filled in")
and a technical readout ("what does it actually say"). `Next Deadline`,
`Last Reminder Sent`, and `Reminder Count` are computed automatically;
`Status` only turns green once every rule for that asset passes. A second
**Audit Log** sheet keeps the full one-row-per-violation trail for anyone
who needs to trace a flag back to its exact rule and value.

**The tracker is a living document, not a disposable snapshot.** The
`Notes / Follow-up` column is config-declared free text — type into it
directly in Excel, save, and re-run the tool: your note survives while every
computed column refreshes to the latest validation. This is proven by test
(`test_notes_survive_regeneration_for_asset_still_present` in
[`test_excel_report.py`](tests/test_excel_report.py)), not just claimed.

## Document archive: checking what's actually been filed, not just what's typed in

Compliance data doesn't arrive pre-typed into a spreadsheet — it arrives as
PDFs. `document_on_file` rules check a real archive for the expected file per
asset, so "insurance certificate not filed" is a real check against what's
actually on file, not a proxy field. The CLI checks a local archive folder
(`data/energy_assets_documents/`, genuinely valid synthetic PDFs generated by
[`scripts/generate_sample_documents.py`](scripts/generate_sample_documents.py));
the web app checks a Supabase Storage bucket instead, since a hosted app has
no durable local disk. Both go through the same
[`DocumentArchive`](src/compliance_tracker/archive.py) interface — rule
evaluation never touches a filesystem or the `supabase` package directly, and
lists each rule's archive directory exactly once per validation run (not once
per asset), so checking a large portfolio never costs one network round-trip
per asset per rule.

## Automated intake: from a raw inbox to structured values

The remaining manual step was the biggest one: someone still had to open
every PDF and retype the expiry date or percentage into the CSV. `intake`
closes that gap — but only for *extraction*, never for the compliance
decision itself:

```bash
pip install -e ".[llm]"
python -m compliance_tracker intake --config config/energy_assets.yaml \
    --inbox-dir data/energy_assets_inbox
```

Drop raw files into an inbox folder — arbitrary filenames, exactly as a
client would actually send them, **PDF, Excel, or a photo/scan**
(`data/energy_assets_inbox/` ships PDF and Excel examples:
`IMG_20260810_permit_scan.pdf`, `Windridge_GridCert_Renewal.pdf`, and
`AST-011_insurance_schedule.xlsx`). For each file,
[`extraction.py`](src/compliance_tracker/extraction.py) dispatches by file
extension: PDFs and Excel files go through fully deterministic text
extraction first (`pypdf` / `openpyxl`, no AI involved in this step); a jpg
or png photo skips text extraction entirely and goes straight to Claude as a
vision input — there's no separate OCR library, since the same model already
classifying the document can just read the image. Either way, Claude returns
which known asset the document belongs to, which document type it is, and
the values for whatever fields that document type declares as extractable in
config — e.g. `insurance_doc_on_file` declares `insurance_expiry: date`. A
confident match gets auto-filed into the document archive under the naming
convention the existing `document_on_file` rules already check, and its
extracted values are appended to a separate, clearly-labeled overlay layer
(`output/<domain>/extracted_values.csv` for the CLI, an `extracted_values`
Supabase table for the web app) — never a silent edit to the base CSV
registry, so it's always visible which values a human entered and which an
LLM read off a document. An unconfident match (or a file with an extension
it doesn't recognize, e.g. `.docx`) is left for a human to resolve — the CLI
leaves it in the inbox folder, the web app files it under a `_pending_review`
prefix in the Storage bucket — and logged either way. Intake never guesses.

The CLI's `run_intake()` and the web app's upload widget both call the same
per-file step, [`intake.process_upload()`](src/compliance_tracker/intake.py)
— parameterized by whichever `DocumentArchive` and extracted-values sink the
caller passes in, so this logic isn't duplicated between the two.

**The LLM's job stops at extraction.** `run`/`sync` merge the overlay on top
of the base registry ([`extracted_values.py`](src/compliance_tracker/extracted_values.py))
and hand the merged record to the same unchanged deterministic rule engine —
every extracted value still gets shape-checked (a `date`-typed field that
doesn't parse as ISO 8601 is dropped, not written) before it can affect a
compliance decision. `intake` is its own subcommand, not bundled into
`run`/`sync`, since it costs real API calls and shouldn't fire just because
someone wants to regenerate a tracker. `extraction.py`/`intake.py` are fully
unit-tested against an injectable client — no network calls or
`anthropic`/`pypdf` install required to run the test suite (only to actually
call `intake` for real, which needs `ANTHROPIC_API_KEY` set).

## CLI alternatives: local Excel, or a live Google Sheet

The web app above is the primary interface; these two commands remain for
cases that don't need it (CI-friendly checks, a quick local export, no
Supabase project set up yet).

**`run`** — zero external setup, writes a local `.xlsx`. This is the
zero-dependency path used for the domain-swap proof below.

**`sync`** — pushes the same Tracker into a live Google Sheet instead, and
can send real reminder emails:

```bash
pip install -e ".[sheets]"
python -m compliance_tracker sync --config config/energy_assets.yaml \
    --sheet-id <your-sheet-id> --send
```

This exists because a local Excel file fights you the moment two things want
to touch it at once — this project hit that exact `PermissionError` mid-build
when re-running the tool against a tracker that was still open in Excel.
A Google Sheet is a cloud-hosted document: no local file, no lock, and it's a
tool teams already use, so there's no migration cost either. `sync` requires
a Google Cloud service account (see
[`sheets_sync.py`](src/compliance_tracker/sheets_sync.py)'s module docstring
for setup); the integration itself is fully unit-tested against a fake
in-memory client, so none of its logic depends on real credentials existing.

Real email sending (`--send`, or `send_drafted_emails()` directly) stays
opt-in: it only fires with `EMAIL_SEND_MODE=live` plus `SMTP_HOST` /
`SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` set as environment variables. No
credentials are ever hardcoded, and CI never exercises the live-send path.
Drafts are always written locally first regardless of which command you run.

## Proof: the same engine, two unrelated domains

The whole premise of this project is that swapping domains costs a config
file, not a code change.

**Domain 1 — energy assets**: fields like `certification_expiry`,
`insurance_expiry`, `margin_pct`, plus the document archive above.

**Domain 2 — early-stage portfolio companies**
([config/portfolio_companies.yaml](config/portfolio_companies.yaml)): fields
like `runway_months`, `last_board_update`, `cap_table_current` — chosen
because it's the same "many distributed things need recurring compliance
checks" pattern a growth fund would recognize from its own portfolio.

```
$ python -m compliance_tracker run --config config/portfolio_companies.yaml
Domain: Portfolio Companies
Assets loaded: 15
Flagged: 12 (3 compliant)
Excel tracker: output\portfolio_companies\tracker.xlsx
Email drafts: 12 written to output\portfolio_companies\emails
Reminder log: output\portfolio_companies\reminder_log.csv
```

Same `src/compliance_tracker/` code both times. The Tracker columns differ
entirely because they're read from each config, not hardcoded:

```
Portfolio Tracker: Company ID | Company Name | Stage | Founder Contact
| Financials Audited | Audit Report on File | Cap Table Current | Cap Table Export on File
| Last Board Update | Runway (months) | Data Room Ref | Data Room Ref Format | Funding Stage
| Next Deadline | Last Reminder Sent | Reminder Count | Status | Notes / Follow-up
```

`tests/test_config_swap.py` runs both configs through the full pipeline and
asserts on exactly this — including a regression guard that fails the build
if anyone ever hardcodes a domain name into the validator or report code.

## Config schema

One YAML file per domain describes everything:

```yaml
domain: "Energy Assets"

source:
  type: csv                 # swappable: Airtable/API/DB loaders implement
  path: "data/..."          # the same AssetLoader interface (loaders.py)
  id_field: asset_id

contact:
  name_field: contact_name  # who gets the follow-up email
  email_field: contact_email

rules:
  - id: cert_current
    field: certification_expiry
    type: not_expired
    max_age_days: 0
    severity: critical
    label: "Grid Cert Expiry"     # this rule's Tracker column header
    message: "Grid connection certification expired on {value}"

  - id: insurance_doc_on_file
    type: document_on_file
    directory: "data/energy_assets_documents"
    filename_pattern: "{asset_id}_insurance_certificate.pdf"
    severity: critical
    label: "Insurance Cert on File"
    message: "Insurance certificate not found in document archive"
  # ...

excel:
  info_columns: [...]       # identity/context columns shown first
  notes_columns:            # free-text, preserved across runs
    - {field: notes, label: "Notes / Follow-up"}

email:
  subject_template: "..."
  body_template: "..."      # supports {reminder_number}
```

See [config/energy_assets.yaml](config/energy_assets.yaml) and
[config/portfolio_companies.yaml](config/portfolio_companies.yaml) for the
full working examples. Config loading fails fast with a specific error
message (missing key, unknown rule type, bad severity, id column mismatch)
rather than silently producing a broken run — see
[`config_schema.py`](src/compliance_tracker/config_schema.py).

## Reminder history

Every `run`/`sync` appends to a persistent `reminder_log.csv` for every
asset still flagged that cycle, so `Last Reminder Sent` and `Reminder Count`
reflect real accumulated history rather than resetting each time — run the
tool three times in a row and watch the count climb
([`reminder_log.py`](src/compliance_tracker/reminder_log.py)). Email drafts
reference this via `{reminder_number}`, so a draft can say "this is
follow-up reminder #3."

Drafts also state an actual **deadline** — "resolve the above before
2027-01-15, your next compliance deadline" — computed from the asset's own
`not_expired` rules, but only counting rules that are *actually violated*.
An asset flagged only for, say, a maintenance-status issue never cites an
unrelated (and still-compliant) certificate's expiry date as if it were the
deadline for that flag — `email_drafter.py`'s `_violation_deadline()` scopes
strictly to what's actually being flagged, verified by a regression test
(`test_draft_email_ignores_unrelated_compliant_deadline`) written after
exactly that bug showed up in a real generated email during development.

## Tests

```bash
pytest -v
```

124 tests covering: each of the seven rule types at their boundaries
(`document_on_file` against an injected `DocumentArchive`, not a real
filesystem), the CSV loader's error handling, end-to-end validation counts,
config-driven Tracker column mapping, notes-preservation across
regeneration (the local Excel path, the Google Sheets path, and the
Supabase path — the latter two each via a fake in-memory client, no
`gspread`/`supabase` install required), PDF, Excel, and image dispatch in
`extraction.py`, deadline-line correctness in reminder emails (including the
regression test above), reminder-log accumulation, the shared
scheduler-agnostic reminder cycle (`reminders.py`), draft-only-by-default
email behavior with SMTP mocked for the opt-in send path, document
classification/extraction and the intake pipeline (against fake LLM
clients — no network calls or `anthropic` install required), the
extracted-values overlay (both the CSV and Supabase-table-backed paths), the
dynamic table filters (`filters.py`, pure pandas, no `streamlit` install
required), and the domain-swap proof above. GitHub Actions
([.github/workflows/tests.yml](.github/workflows/tests.yml)) runs the suite
on every push and PR against Python 3.11 and 3.13.

## Automated reminders on a schedule

`app.py`'s "Draft reminders" button only runs when someone clicks it. To
send reminders unattended, [`scripts/send_reminders.py`](scripts/send_reminders.py)
wraps the exact same logic
([`reminders.run_reminder_cycle()`](src/compliance_tracker/reminders.py)) in
a plain, dependency-free command:

```bash
python scripts/send_reminders.py --config config/energy_assets.yaml --send
```

This is deliberately not tied to any particular scheduler — which one you
use depends on where you end up hosting the app, and that's not decided yet.
Once it is, point whichever scheduler you pick (cron, a GitHub Actions
workflow on a schedule, Windows Task Scheduler, a long-running process) at
this command with `SUPABASE_URL`/`SUPABASE_KEY`/`EMAIL_SEND_MODE`/`SMTP_*`
set — no further code changes needed.

## Roadmap (not built)

One thing is deliberately out of scope today, explicitly *future* rather
than partially built:

- **Deploying the web app to a public URL, and picking a scheduler for the
  command above.** `app.py` runs locally today (`streamlit run app.py`); the
  data and filed documents already live in Supabase rather than local disk
  specifically so this doesn't require a second migration once a hosting
  target (e.g. Streamlit Community Cloud) and a scheduler are chosen.

## Project structure

```
app.py                           # Streamlit web app -- the primary interface
.env.example                     # every env var the app/CLI reads, documented
config/                          # one YAML per domain
data/                            # synthetic sample CSVs, document archives, inbox samples
scripts/generate_sample_documents.py  # (re)generates the synthetic PDF archive + inbox samples
scripts/send_reminders.py        # scheduler-agnostic entrypoint for unattended reminder sending
src/compliance_tracker/
  loaders.py                     # AssetLoader ABC + CSVLoader
  rules.py                       # the 7 generic rule-type checks
  validator.py                   # orchestrates load + rule evaluation
  archive.py                     # DocumentArchive: local-disk (CLI) or Supabase Storage (web app)
  excel_report.py                # config-driven Tracker/Audit Log Excel generation
  sheets_sync.py                 # syncs the same Tracker to a live Google Sheet
  database.py                    # syncs the same Tracker to Supabase, backs the web app
  reminder_log.py                # persistent reminder history (CSV-backed CLI path)
  reminders.py                   # shared reminder-evaluate-draft-send cycle (app button + script)
  extraction.py                  # LLM classify/extract from a single document (injectable client)
  extracted_values.py            # persistent overlay of extracted values on the base registry
  intake.py                      # orchestrates inbox/upload -> archive + extracted values
  filters.py                     # config-free dynamic table filters for the web app
  email_drafter.py               # draft-to-file, opt-in SMTP send
  config_schema.py               # config loading + validation
  cli.py                         # `run` (local Excel) / `sync` (Google Sheet) / `intake` (LLM extraction)
tests/                           # pytest suite, incl. the domain-swap proof
.github/workflows/tests.yml      # CI
```
