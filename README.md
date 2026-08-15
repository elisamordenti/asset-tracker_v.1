# Asset Compliance Validator

A config-driven engine that checks hundreds of distributed "assets" against a
compliance rulebook, builds a centralized Excel tracker, and drafts follow-up
emails for whatever's missing — with zero code changes required to point it
at a completely different domain.

## The problem this replaces

In a consulting role, I worked alongside renewable energy producers managing
hundreds of EU/US solar and wind assets. Regulatory compliance is critical
for bank financing and winning tenders: each asset needs current
documentation, accurate margin and maintenance records, and specific
technical criteria met. Checking compliance and assembling the data room for
banks was a manual, error-prone process that took **about three weeks**, most
of it spent chasing clients for missing information.

This project rebuilds that workflow as a generic, auditable pipeline:

| | Before | After |
|---|---|---|
| Checking every asset against the rulebook | Manual, spreadsheet by spreadsheet | One command, deterministic |
| Building the bank/tender data room | ~3 weeks | ~1 day |
| Knowing *why* something was flagged | Tribal knowledge | Every flag traces to one rule + one value in an Issues Log |
| Chasing clients for missing info | Ad hoc emails | Auto-drafted, one per flagged asset, ready to send |
| Adapting to a new asset type / schema | Rewrite the spreadsheet macros | Edit a YAML file |

All data in this repo is synthetic. Nothing here comes from a real client.

## How it works

```
CSV (or any future source) --> Loader --> Validator --> Excel Tracker
                                              |            (Summary /
                                        Rules (YAML)        Asset Detail /
                                              |              Issues Log)
                                              v
                                       Email Drafts (.txt)
```

Every domain-specific decision — which fields exist, which rules apply, what
the Excel columns are called, how the follow-up email reads — lives in one
YAML config per domain. The engine code (`src/compliance_tracker/`) never
mentions a domain by name.

**The validation engine is deterministic and rule-based, not an LLM.** Every
rule is one of six generic, auditable check types:

| Type | Checks | Example use |
|---|---|---|
| `required` | Field is non-empty | Missing permit document |
| `not_expired` | Date field isn't older than today + a grace period | Expired certification |
| `min_value` | Numeric field ≥ a threshold | Margin below minimum |
| `max_value` | Numeric field ≤ a threshold | Headcount over budget |
| `allowed_values` | Field is one of a fixed set | Invalid status value |
| `regex_match` | Field matches a format | Malformed document reference |

A human can read this list and know exactly what the system can and can't
check. That's a deliberate boundary, not an oversight: swapping *which*
fields, thresholds, and messages apply to *any* domain never touches code —
only YAML does. Adding a genuinely new *kind* of check (e.g. comparing two
fields to each other) would need one small function added to
[`rules.py`](src/compliance_tracker/rules.py); that's the one place domain
logic could ever leak into code, and it hasn't needed to for either domain
below.

## Quickstart

```bash
pip install -e ".[dev]"

python -m compliance_tracker run --config config/energy_assets.yaml
python -m compliance_tracker run --config config/portfolio_companies.yaml

pytest
```

Each run writes `output/<domain>/tracker.xlsx` (Summary, Asset Detail, and
Issues Log sheets) and `output/<domain>/emails/*.txt` (one draft per flagged
asset). `output/` is gitignored — nothing generated is committed.

## Proof: the same engine, two unrelated domains

The whole premise of this project is that swapping domains costs a config
file, not a code change. Rather than just claim that, here it is running
against two datasets with entirely different fields:

**Domain 1 — energy assets** ([config/energy_assets.yaml](config/energy_assets.yaml), [data/energy_assets_sample.csv](data/energy_assets_sample.csv)):
fields like `certification_expiry`, `insurance_expiry`, `margin_pct`.

```
$ python -m compliance_tracker run --config config/energy_assets.yaml
Domain: Energy Assets
Assets loaded: 18
Flagged: 12 (6 compliant)
Excel tracker: output\energy_assets\tracker.xlsx
Email drafts: 12 written to output\energy_assets\emails
```

**Domain 2 — early-stage portfolio companies** ([config/portfolio_companies.yaml](config/portfolio_companies.yaml), [data/portfolio_companies_sample.csv](data/portfolio_companies_sample.csv)):
fields like `runway_months`, `last_board_update`, `cap_table_current` — chosen
because it's the same "many distributed things need recurring compliance
checks" pattern a growth fund would recognize from its own portfolio.

```
$ python -m compliance_tracker run --config config/portfolio_companies.yaml
Domain: Portfolio Companies
Assets loaded: 15
Flagged: 9 (6 compliant)
Excel tracker: output\portfolio_companies\tracker.xlsx
Email drafts: 9 written to output\portfolio_companies\emails
```

Same `src/compliance_tracker/` code both times. The Excel column headers
differ entirely because they're read from each config, not hardcoded:

```
Energy Assets Summary sheet:      Asset ID, Asset Name, Status, Critical Issues, Warnings, Responsible Contact
Portfolio Companies Summary sheet: Company ID, Company Name, Status, Critical Issues, Warnings, Founder Contact
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
    message: "Grid connection certification expired on {value}"
  # ...

excel:
  summary_columns: [...]    # column -> label, drives the Summary sheet
  detail_columns: [...]     # column -> label, drives the Asset Detail sheet

email:
  subject_template: "..."
  body_template: "..."
```

See [config/energy_assets.yaml](config/energy_assets.yaml) and
[config/portfolio_companies.yaml](config/portfolio_companies.yaml) for the
full working examples. Config loading fails fast with a specific error
message (missing key, unknown rule type, bad severity) rather than silently
producing a broken run — see [`config_schema.py`](src/compliance_tracker/config_schema.py).

## Excel tracker

Three sheets, generated purely from config:

1. **Summary** — one row per asset: status, critical/warning counts, and
   responsible contact. Flagged assets sort to the top.
2. **Asset Detail** — one row per asset with the raw record fields chosen in
   `excel.detail_columns`.
3. **Issues Log** — one row per rule violation (asset, rule, severity, field,
   value, rendered message). This is the audit trail: every flag on the
   Summary sheet traces back to specific rows here.

## Email drafting

For every flagged asset, a `.txt` draft is written to
`output/<domain>/emails/` using the config's subject/body templates and the
contact fields from that asset's record. **Draft-to-file is the only thing
that happens by default.** Real sending
([`send_drafted_emails`](src/compliance_tracker/email_drafter.py)) is a
separate, explicit opt-in: it only runs if `EMAIL_SEND_MODE=live` plus all of
`SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` are set as
environment variables. No credentials are ever hardcoded, and CI never
exercises the live-send path.

## Tests

```bash
pytest -v
```

43 tests covering: each of the six rule types at their boundaries (e.g. a
value exactly at a threshold passes, one unit past it fails), the CSV
loader's error handling, end-to-end validation counts, config-driven Excel
column mapping, draft-only-by-default email behavior (with SMTP mocked for
the opt-in send path), and the domain-swap proof above. GitHub Actions
([.github/workflows/tests.yml](.github/workflows/tests.yml)) runs the suite
on every push and PR against Python 3.11 and 3.13.

## Roadmap (v2, not built)

The current pipeline expects clean, structured CSV rows. A natural next step
— explicitly **future work, not part of this repo today** — is an optional
LLM-based extraction module that turns messy unstructured input (scanned
PDFs, inspection reports, email threads) into the structured fields this
validator already expects, then hands off to the exact same deterministic
rule engine unchanged. The core compliance logic would stay 100%
rule-based and auditable; the LLM's job would be limited to extraction, never
to deciding compliance itself.

## Project structure

```
config/                          # one YAML per domain
data/                            # synthetic sample CSVs
src/compliance_tracker/
  loaders.py                     # AssetLoader ABC + CSVLoader
  rules.py                       # the 6 generic rule-type checks
  validator.py                   # orchestrates load + rule evaluation
  excel_report.py                # config-driven Excel generation
  email_drafter.py               # draft-to-file, opt-in SMTP send
  config_schema.py                # config loading + validation
  cli.py                          # `python -m compliance_tracker run ...`
tests/                            # pytest suite, incl. the domain-swap proof
.github/workflows/tests.yml       # CI
```
