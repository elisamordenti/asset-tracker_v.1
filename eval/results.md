# Extraction pipeline: eval results

**Not yet run.** This file is checked in as a template so the structure of
the measurement is visible without needing a live Claude API call.

To generate real numbers:

```
export ANTHROPIC_API_KEY=sk-...
python eval/run_eval.py
```

This overwrites this file with real output: a verdict for each of the 14
fixtures in `eval/fixtures/` (`MATCH` / `FALSE_NEGATIVE` / `FALSE_POSITIVE`),
whether the deterministic router resolved it without an LLM call, and
whether the model's own citation for each extracted value actually verifies
against the source document. See `eval/run_eval.py`'s module docstring for
exactly what each of those means and why.

## What the fixtures stress

| failure_mode | what it tests |
|---|---|
| clean_baseline | sanity check -- also the case the deterministic router should resolve with no LLM call |
| ambiguous_dates | two dates on one document, no single unambiguous label |
| missing_field | the field is genuinely absent -- can the model say "no value" instead of guessing? |
| malformed_date | a non-ISO date format |
| unusual_label | the expiry appears under a label the schema doesn't literally name |
| no_asset_reference | only an informal nickname, no asset id at all -- correct behavior is `asset_id: null` |
| decoy_value | a plausible but wrong date sits right next to the real one |
| multi_row_excel | a spreadsheet covering several known assets in one file |
| conflated_assets | one document explicitly covering two known assets |
| wrong_domain | a document that belongs to no known type at all |
| truncation_cutoff | the real field is pushed past the model's existing 20,000-character truncation limit -- a real, already-existing limitation, not a hypothetical one |
| prompt_injection | text in the document body reading like an instruction to the model |
| noisy_text | irregular spacing simulating a rough text-extraction pass |

## Known limitation this eval is expected to surface

`intake.py`'s `CONFIDENT_LEVELS = {"high", "medium"}` treats medium-confidence
extractions the same as high-confidence ones for auto-filing purposes. That
threshold has never been measured against real failure data. Once this eval
has been run against a larger, real sample of traffic (not just these 14
adversarial fixtures), the FALSE_NEGATIVE rate broken out by confidence level
is the right evidence to revisit it with -- not a speculative change made
without that evidence.
