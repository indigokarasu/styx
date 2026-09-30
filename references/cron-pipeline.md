# Cron Pipeline Notes

Detail behind the four-step daily run in SKILL.md. Read this before scheduling
the pipeline or debugging a cron result that looks wrong.

## Canonical runner

`~/.hermes/scripts/rr_styx_enrich.sh` runs all four steps in the correct order
using `/root/hermes-agent/.venv/bin/python`. **Prefer running it** over invoking
the steps by hand — the ordering is load-bearing (see Step 0 below).

## Steps

| # | Script | Skill | Does |
|---|--------|-------|------|
| 0 | `seed.py` | styx | Links new Plaid transactions to merchants |
| 1 | `styx_universal_enrich.py` | styx | Google Places enrichment, all categories |
| 2 | `taste_full_enrich.py` | taste | Ingests enriched merchants into Taste |
| 3 | `safe_taste_dedup.py` | taste | Dedups same-day Taste signals |

## Why Step 0 must run first

Without seeding, enrichment only sees merchants that were *already* linked, so
it reports "Enriched: N" against stale rows and the Styx→Taste pipe silently
dries up. This is easy to miss precisely because the run looks successful.

Verification, independent of the enrichment tally:

```sql
SELECT COUNT(*) FROM transaction_merchants;
```

Compare against the transaction count in `transactions.db`. As of 2026-09-27,
2,173 of 2,365 transactions were linked. Of the 219 unlinked, 173 fall in
financial categories Styx intentionally skips; most of the remainder are
non-merchants — `CASH BACK`, fully redacted `***********` names, HOA checks —
that Google Places cannot resolve. **A non-zero unlinked count is normal.**

## Script paths

- Styx scripts: `~/.hermes/profiles/indigo/skills/ocas-styx/scripts/`
- Taste scripts: `~/.hermes/profiles/indigo/skills/ocas-taste/scripts/`
- **Not** `~/.hermes/commons/data/ocas-styx/` — that directory holds only
  `config.json`.
- **Not** `~/.hermes/commons/data/ocas-taste/scripts/` — that holds only
  `styx_recent_delta.py`.

## Dedup: use `safe_taste_dedup.py`

`taste_signals_dedup.py` does not exist. Do not substitute
`dispatch_taste_dedup.py`: it keys on `event_date[:10]` while Styx signals carry
`date`, so it collapses every Styx signal to one per venue and deletes the rest.

`safe_taste_dedup.py` backs up `signals.jsonl` before writing and refuses to
write if the Styx count would drop.

## Expected behaviours that are NOT errors

- **5–15 merchants re-enriched with no new transactions.** These are
  already-enriched merchants being re-queried against Places. `no_result` is
  expected for heavily obfuscated names such as `DD *DOORDASH *********` or
  `SP THANKS ICON`.
- **`taste_full_enrich.py` reporting "Failed: N"** for items needing LLM
  resolution. Known cron limitation — `llm_resolve.py` calls `hermes ask`,
  which returns nothing without an interactive session. Retried on the next
  non-cron run.
- **HTTP 429 from Google Places is NOT that limitation.** A 429 means quota was
  genuinely exhausted. Observed 2026-09-27: 61 of 160 items failed with 429 in
  `taste_full_enrich.py` while `styx_universal_enrich.py` in the same run had
  none. Those merchants stay pending. Slow the request rate or split the batch;
  do not retry harder.

## Report format

After a cron run, report: merchants enriched by category, new Taste items
created, signals deduped, and any errors. State the linkage count separately
from the enrichment count — they measure different things and a run can enrich
plenty while linking nothing.

## Known broken jobs

Two live cron jobs (`styx:enrich-new-transactions` 07:30,
`taste:daily-styx-enrichment` 08:00) instruct the agent to run
`~/.hermes/commons/data/ocas-styx/styx_universal_enrich.py`, which does not
exist. Both also reference `rr_styx_enrich.sh`, which does. Full write-up and
the required operator fix: `enrichment-known-issues.md` §3.
