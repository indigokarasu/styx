# Universal Enrichment (Google Places, all categories)

`styx_universal_enrich.py` is the **default** enrichment script. It covers every
non-financial category. The older `styx_places_enrich.py` is food-only and
misses retail, medical, transport, and everything else — prefer universal unless
you have a specific reason not to.

**Script:** `scripts/styx_universal_enrich.py` (in this skill, not in `commons/`).

## Usage

```bash
# All pending non-food merchants (default limit 50)
python3 scripts/styx_universal_enrich.py

# Everything pending, no cap
python3 scripts/styx_universal_enrich.py --limit 0

# Re-query merchants already marked source='google_places'
python3 scripts/styx_universal_enrich.py --all

# Rehearse without writing — prints the same plan, touches nothing
python3 scripts/styx_universal_enrich.py --dry-run
```

| Flag | Default | Effect |
|------|---------|--------|
| `--limit N` | 50 | Max merchants this run. `0` means no cap. |
| `--dry-run` | off | No DB writes, no enrichment_runs row. |
| `--all` | off | Include merchants already enriched by Places. |

Requires `GOOGLE_PLACES_API_KEY` in `~/.hermes/secrets/plaid.env`; the script
exits 1 with a named error if it is absent. Every script here supports
`--help` and exits 0 without side effects — safe to probe in a cron audit.

## What it covers

`retail`, `service`, `entertainment`, `transport`, `personal_care`, `medical`,
`home`, `government`, `housing`, `travel`, plus all food subcategories
(`restaurant`, `cafe`, `bar`, `bakery`, `grocery`, `supermarket`, `takeaway`,
`delivery`, `convenience`, `liquor_store`).

## What it skips, and why

`SKIP_CATEGORIES` = `transfer`, `transfer_in`, `transfer_out`, `income`,
`bank_fees`, `loan_payments`, `loan_disbursements`, `rent_and_utilities`.

These have no physical location — Google Places can only return a wrong answer
for them, and each lookup costs quota. Skipped rows stay at their existing
enrichment (`source: 'internal'` where nothing else resolved them).

**Two namespaces, deliberately.** `CATEGORY_MAP` in `styx_common.py` is keyed on
Plaid's UPPERCASE `personal_finance_category` (`LOAN_PAYMENTS`). `SKIP_CATEGORIES`
filters `merchants.category`, which stores the **lowercased** value
(`loan_payments`). They are not the same vocabulary and the skip list is correct
as written. Making them match by uppercasing the skip list would silently
un-skip every financial row and burn Places quota on bank transfers — a test
pins this (`test_skip_categories_use_the_stored_lowercase_namespace`).

## Reading the output

```
DRY RUN — Enriching 12 merchants via Google Places...
  ✓ [restaurant       ] Blue Bottle Coffee               14v  Blue Bottle Coffee @ San Francisco, CA
  ✗ [retail           ] TGT*-CVS 0912                     3v  no_result
  ...
  Enriched: 9
  Failed:   3
```

- `✗ no_result` is **expected** for heavily obfuscated names. The merchant keeps
  whatever enrichment it already had (searxng, plaid_merchant_name, internal).
- `Failed: N` is not necessarily an error — see Gotchas below.

## Known defects (pinned by tests)

Two `clean_name` regressions mean some merchants can never resolve. They are
**not** patched here because fixing them changes what the pipeline sends to
Google; full write-up in `enrichment-known-issues.md`.

1. `DD *DOORDASH ROYALINDI` → `ROYALINDI`. The regex alternative
   `DD\s+\*DOORDASH\s+` is greedy and eats the brand token it was meant to keep.
2. `SP THANKS ICON` → `THANKS`. The short-tail truncation rule strips ALL-CAPS
   location markers.

Evidence from the live DB: 760 merchants carry `source='google_places'`, and
**zero** `DD*`-prefixed merchants do.

## Gotchas

- **No new transactions ≠ no work.** A run with no new Plaid data can still
  re-enrich 5–15 merchants. That is the re-query of already-linked merchants,
  not a bug.
- **HTTP 429 is a quota condition, not the cron LLM limit.** A 429 from Places
  means the quota is genuinely exhausted; those merchants stay pending for the
  next run. The fix is to slow the request rate or split the batch — not to
  retry harder. Distinct from the known `llm_resolve.py` cron no-op.
- **`taste_full_enrich.py` 429 is a different failure.** Observed 2026-09-27:
  61 of 160 items failed with 429 from Places while universal enrichment in the
  same run had none. Do not classify either as the LLM-in-cron limitation.
- **Enrichment is not linkage.** "Enriched: N" says nothing about whether new
  transactions are wired to merchants. Only `seed.py` writes
  `transaction_merchants` rows — see the Linkage section of SKILL.md.
- **Rate limit is 0.25 s per merchant** in `main()`. Tightening it is what
  causes 429s; loosening it costs wall-clock.
- Name cleaning is in `clean_name()`, address parsing in
  `parse_formatted_address()` (handles US zip, UK postcode, city-only, and
  international). Both are covered by tests.
