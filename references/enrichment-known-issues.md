# Enrichment Known Issues

Open defects in the enrichment path, with evidence and the reasoning for why
each is documented rather than silently patched. Each is pinned by a test in
`tests/test_styx_common.py` so it cannot change without someone noticing.

## 1. `clean_name` destroys the brand token on delivery prefixes

**Severity:** high — these merchants can never be enriched.

`styx_universal_enrich.py` `_PREFIX_RE` contains the alternative
`DD\s+\*DOORDASH\s+`. It is greedy, so on the real Plaid string it consumes the
brand token it was meant to preserve:

| Input | Output | What happened |
|-------|--------|---------------|
| `DD *DOORDASH ROYALINDI` | `ROYALINDI` | brand "DOORDASH" eaten |
| `SQ *Blue Bottle 1234` | `Blue Bottle 1234` | correct (this alternative is fine) |
| `ABM-350 MISSION GARAGE` | `350 MISSION GARAGE` | correct |
| `TGT*-CVS 0912` | `-CVS 0912` | leading hyphen left in place |

`ROYALINDI` is a strictly worse Places query than the original string, and no
amount of retrying recovers it.

**Evidence from the live DB (2026-09-29):** 760 merchants carry
`source='google_places'`. Zero `DD*`-prefixed merchants do. The only `SP*` or
`ABM-` prefixed merchant is `SP THANKS ICON` at `source='searxng'`,
`confidence=0.6`, `category='other'` — never reached Places at all.

**Why not patched here:** the narrow fix is to make the alternative lazy and
word-bounded, but the correct fix depends on a product decision — should
"Doordash Royal India" resolve to the *restaurant* (which needs the brand kept)
or to a *delivery platform* (which needs the platform kept)? Changing this
changes what the pipeline sends to a paid API, so it is reported, not guessed.

Pinned by: `test_greedy_delivery_prefix_eats_the_brand`.

## 2. Short-tail truncation eats ALL-CAPS location markers

**Severity:** medium — narrows queries, sometimes still resolves.

The rule `re.sub(r"\s+[A-Z]{1,4}$", "", n)` exists to remove truncated Plaid
cutoffs (`CLOTHIN` → `Clothin`). It cannot tell a truncation from a location
marker, so `SP THANKS ICON` → `THANKS`.

Pinned by: `test_trailing_uppercase_suffix_truncation`.

## 3. Two live cron jobs name a script path that does not exist

**Severity:** high — the scheduled jobs are broken, not the skill.

`styx:enrich-new-transactions` (07:30) and `taste:daily-styx-enrichment`
(08:00) both instruct the agent to run
`/root/.hermes/commons/data/ocas-styx/styx_universal_enrich.py`. That directory
contains only `config.json`; the script is not there. The real script is
`scripts/styx_universal_enrich.py` in this skill.

Both jobs do also reference the shared runner
`/root/.hermes/scripts/rr_styx_enrich.sh`, which exists and runs all four
pipeline steps in the correct order.

**Not fixed here:** editing cron schedules is outside a skill critique, and
`jobs.json` is managed by a separate repair path. Reported to the operator.
The correct fix is to repoint both jobs at `rr_styx_enrich.sh`.

## 4. `RENT_AND_UTILITIES` appears in two vocabularies

Not a bug — a trap for anyone "cleaning up" the code. `CATEGORY_MAP` keys on
Plaid's uppercase `personal_finance_category`; `SKIP_CATEGORIES` filters the
lowercased `merchants.category` column. Making them match by uppercasing the
skip list would un-skip every financial row and spend Places quota on bank
transfers. Pinned by
`test_skip_categories_use_the_stored_lowercase_namespace`.
