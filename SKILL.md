---
license: MIT
name: ocas-styx
description: >-
  Transaction data store with merchant enrichment. Enriches garbled/obfuscated
  bank transaction names into real business entities via Google Places, SearXNG
  and LLM resolution, and syncs transactions and balances from the Plaid API.
  Other skills (Taste, Rally, Vesper, Sands) read Styx for consumption signals,
  spending analysis and pattern detection. Use when enriching merchant names,
  querying what was spent where, syncing bank transactions, or writing a
  consumer query against the Styx DB. NOT for creating transactions (use the
  bank directly), budgeting strategy (use Rally), or email-based consumption
  scanning (use Taste).
source: https://github.com/<agent-handle>/styx
includes:
- references/**
- scripts/**
- tests/**
metadata:
  author: Indigo Karasu (indigokarasu)
  version: "1.6.0"
  hermes:
    category: data-science
    tags:
    - transactions
    - finance
    - merchant-enrichment
    - banking
triggers:
- transaction data
- bank transactions
- merchant enrichment
- financial data store
- query transactions
- what did I spend
- spending analysis
- plaid sync
---

# Styx — Transaction Data Store

Styx is the system's transaction intelligence layer. It sits between raw bank
data (Plaid, via financial-sync) and consumer skills that need clean merchant
information (Taste, Rally, Vesper, Sands).

## When to Use

- Enriching garbled/obfuscated transaction names into real business entities
- Merchant lookup and business matching from transaction data
- Answering "what did I spend" or "where did I spend" questions
- Pulling/syncing bank transactions via Plaid API
- Spending analysis, pattern detection, or calendar-based spending context
- Providing clean merchant data to consumer skills (Taste, Rally, Vesper, Sands)
- Parsing email receipts (e.g. Rainbow Grocery eReceipts) into `receipt_line_items`

## When NOT to Use

- Budgeting strategy or financial planning (use Rally)
- Email-based consumption scanning (use Taste)
- Creating or modifying transactions (use the bank directly)
- General web research or non-transaction search (use Sift)
- Account management (adding/removing bank links) — use the Plaid Link flow

## Workflow

Styx runs a continuous ingest-enrich-serve loop because raw transaction data
requires normalization before it is useful downstream.

- [ ] **Ingest** — Pull transactions from Plaid (daily cron or on-demand)
- [ ] **Seed** — Link new transactions to merchants (`seed.py`); nothing
      downstream sees a transaction until this runs
- [ ] **Enrich** — Resolve garbled merchant names via Google Places
- [ ] **Store** — Write enriched records to SQLite
- [ ] **Serve** — Expose the query API to consumer skills

Worked example: `UNK MERCHANT 1234` → Places search → `Whole Foods Market` →
stored clean → Taste queries it for spending patterns.

## Core principles

1. **Raw data is sacred** — Plaid transaction records are never modified.
   Enrichment lives in separate tables, linked by `transaction_id`. (Why: a
   destroyed raw record cannot be re-fetched; Plaid history is finite.)
2. **Append-only** — Styx adds records, never deletes them. An enrichment can
   be superseded (marked stale) but not removed. (Why: consumer skills hold
   references to these rows; deleting one breaks their history.)
3. **Read-only for consumers** — other skills query Styx or read the DB
   directly. They do NOT write to Styx tables. (Why: the contract is the only
   enforcement, there is no filesystem permission boundary.)
4. **Enrichment is idempotent** — re-running produces the same result and is
   safe to schedule. (Why: the daily cron must not need a "did it already
   run?" check.)

## Database

Styx keeps its own SQLite DB at `~/.hermes/data/styx.db`. **Hardcode this
path** — do NOT use `{agent_root}`, which resolves to the indigo profile home,
not the shared data directory. Raw Plaid transactions live read-only in
`~/.hermes/data/transactions.db`.

The copy at `~/.hermes/commons/data/ocas-styx/styx.db` is a stale 0-byte stub —
ignore it. That tree holds only `config.json`; **no enrichment script lives
there.**

Schema: `merchants`, `transaction_merchants`, `enrichment_runs`, plus
`receipt_line_items` for parsed receipts. Full DDL and the full path table:
`references/schema.md` and `references/storage-layout.md`.

**The Plaid cursor can get stuck** and silently miss transactions. If
`MAX(date)` is stale, use the `/transactions/get` backfill pattern in
`references/plaid-sync-cursor-recovery.md`; cursors reset after a backfill.

## Enrichment pipeline

`styx_universal_enrich.py` is the **default** — it covers every non-financial
category. The older `styx_places_enrich.py` is food-only.

**Read `references/styx_universal_enrichment.md` first** — flags, the skip
list, how to read the output, and two pinned defects. Rehearse any change with
`--dry-run`, which writes nothing.

Categories skipped (no physical location): `transfer`, `income`, `bank_fees`,
`loan_payments`, `loan_disbursements`, `rent_and_utilities`. These get
`source: 'internal'`.

For names Places cannot resolve, the legacy pipeline runs exact → fuzzy →
SearXNG → LLM → manual review queue (`references/enrichment-pipeline.md`).
`SKIP_CATEGORIES` uses the **lowercase stored** vocabulary, not Plaid's
uppercase one — `references/enrichment-known-issues.md`.

## Daily cron pipeline

Run all four steps in order. The canonical runner is
`~/.hermes/scripts/rr_styx_enrich.sh` — prefer it over running steps by hand.

- [ ] **Step 0 — `seed.py`** (REQUIRED, runs first) — link new Plaid
      transactions into `merchants`/`transaction_merchants`
- [ ] **Step 1 — `styx_universal_enrich.py`** — enrich pending merchants
- [ ] **Step 2 — `taste_full_enrich.py`** — ingest enriched merchants into Taste
- [ ] **Step 3 — `safe_taste_dedup.py`** — dedup same-day Taste signals

Use the `profiles/indigo/skills/...` paths, NOT `commons/data/` (that tree holds
no enrichment script). The dedup script is `safe_taste_dedup.py`;
`taste_signals_dedup.py` does not exist, and `dispatch_taste_dedup.py` keys on
`event_date[:10]` while Styx signals carry `date` — it collapses every Styx
signal to one per venue and deletes the rest. `safe_taste_dedup.py` backs up
`signals.jsonl` first and refuses to write if the Styx count would drop.

**Step 0 fails silently.** Without it, enrichment only sees already-linked
merchants and the Styx→Taste pipe dries up while enrichment still reports
"Enriched: N" on stale merchants (root cause 2026-08-21).

**Linkage ≠ enrichment.** Only `seed.py` writes `transaction_merchants` rows.
Verify with `SELECT COUNT(*) FROM transaction_merchants`.

Expected cron behaviours that are NOT errors, per-step report format, linkage
accounting, and two known-broken cron jobs: `references/cron-pipeline.md`.

## Query API

Consumers read Styx with these patterns: category transactions, spending by
merchant, and unresolved-transaction candidates. Patterns and SQL:
`references/query-api.md`.

## Post-enrichment verification

- [ ] Spot-check 5–10 enriched `transaction_merchants` records at random
- [ ] Confirm the `enrichment_runs` row for this run shows status `completed`
- [ ] Verify `review_queue.jsonl` gained any new low-confidence matches
- [ ] Re-check linkage count, not just the "Enriched" tally

## Financial Sync

- `scripts/plaid_sync.py` — incremental, daily 07:00 cron (`a418e00ee21e`)
- `scripts/plaid_history.py` — full 24-month pull
- Raw DB `~/.hermes/data/transactions.db` is read-only to Styx

Setup and the Plaid Link flow: `references/financial-sync.md`.

## Receipt Parsing Pipeline

Parsed email receipts (e.g. Rainbow Grocery eReceipts) → `receipt_line_items`
(23 columns, but 22 values on INSERT — `id` auto-increments).

- [ ] Fetch via `get_gmail_messages_content_batch`; large results persist to
      `/tmp/hermes-results/<uuid>.txt`
- [ ] Extract the first complete JSON object from the XML wrapper (brace-depth
      counting), then the body between `--- BODY ---` and the next `---`
- [ ] Parse line items: department headers, PLU/UPC codes, prices, weight
- [ ] Insert using `references/receipt-line-items-insert.md`

The Rainbow Grocery format is single-line concatenated and needs a backwards
walk from each price marker. Algorithm, Gmail search patterns, and product
APIs: `references/receipt-parsing.md`.

## Consumer skill contracts

| Skill | Reads Styx for |
|-------|----------------|
| Taste | Restaurants and food businesses absent from email/calendar — `m.category IN ('restaurant','cafe','bar','food')` and `('grocery','supermarket','food_store')`, falling back to `personal_finance_category = 'FOOD_AND_DRINK'`. Writes only its own `signals.jsonl` / `items.jsonl`. |
| Rally | Spending analysis and budget tracking |
| Vesper | Daily/weekly spending summaries in briefings |
| Sands | Calendar-based spending context |

None of them write to Styx.

## Error Handling

| Failure | Symptom | Handling |
|---------|---------|----------|
| Enrichment fails on one merchant | `✗ no_result` | Not an error. The merchant keeps its prior enrichment and is retried next run. |
| Styx→Taste pipe dries up | "Enriched: N" but Taste sees nothing new | Step 0 (`seed.py`) was skipped. Check `SELECT COUNT(*) FROM transaction_merchants`. |
| `Failed: N` on items needing LLM | No output from `llm_resolve.py` | Known cron limitation — it shells out to `hermes ask`, which returns nothing without an interactive session. Retries next non-cron run. |
| HTTP 429 from Places | `HTTP Error 429: Too Many Requests` | Genuine quota exhaustion, **not** the LLM limitation. Merchants stay pending. Slow the rate or split the batch — do not retry harder. |
| `Connection refused` to SearXNG | Enrichment cannot reach it | Port is **8888**, not 8880. Check `docker ps \| grep searx`. |
| `styx.db` has no tables | Every query fails | Created without the schema. Run `seed.py` or `init_styx_db()`. |
| `receipt_line_items` INSERT fails | Column count mismatch | 23 columns, 22 supplied — `id` auto-increments. |
| No new transactions since last sync | Low/zero enrichment | Normal. 5–15 re-enriched merchants is the re-query of linked rows. |
| Redacted names never enrich | `***************` | Correct — skipped by `is_redacted()`. |
| Sync blocked by a dirty tree | `git pull` refuses | Untracked files block the merge. `references/self_update.md`. |
| Merchant never reaches `google_places` | `source` stays un-enriched | `references/enrichment-known-issues.md` — two `clean_name` defects. |
| Data or secrets dir not found | Script cannot open the DB | Post-migration: DBs may sit under `~/.hermes.old/data/`. `references/migration-recovery.md`. |
| Script hangs waiting for input | No output, no exit | Styx scripts take no stdin prompts. Run with `</dev/null`; if it still hangs, the script is not in this skill. |

## Gotchas

- **Name cleaning is essential** — Plaid names are heavily obfuscated
  (`DD *DOORDASH ROYALINDI`, `ABM-350 MISSION GARAGE`). Strip prefixes before
  matching. Two live `clean_name` defects: `references/enrichment-known-issues.md`.
- **`query.py --health-check` does not exist** — use inline Python to check DB
  integrity.
- **Verify signal counts by `extraction_source`, not by grep.** Grepping
  `signals.jsonl` for `styx` matches venue names and nested fields too; the
  real Styx-sourced count is far lower. Only the per-`extraction_source` tally
  answers "did dedup preserve Styx signals".
- **There is no in-skill update path.** Self-update was centralized in the
  `skills:update-fleet` cron. `references/self_update.md` covers recovery when
  a sync hits a dirty tree; `references/historical-incidents.md` holds the
  dated OAuth token-shape and `google_auth_mcp` import-path narratives.

## Support File Map

| File | When to read |
|---|---|
| `references/styx_universal_enrichment.md` | Before running Places enrichment — flags, skip list |
| `references/enrichment-known-issues.md` | When a merchant never reaches `google_places` |
| `references/enrichment-pipeline.md` | Before running or debugging LLM/SearXNG enrichment |
| `references/cron-pipeline.md` | Before scheduling or debugging the daily four-step run |
| `references/cron-gotchas.md` | When a cron result looks like an error but is expected |
| `references/financial-sync.md` | Before configuring Plaid sync or the Link flow |
| `references/plaid-sync-cursor-recovery.md` | When `MAX(date)` has gone stale |
| `references/plaid-gotchas.md` | When a sync returns errors or partial data |
| `references/plaid_ingest_provenance.md` | When auditing where a transaction's values came from |
| `references/plaid_location_backfill.md` | When backfilling merchant city/state/geo |
| `references/schema.md` | Before querying or modifying the database |
| `references/query-api.md` | Before writing a consumer query |
| `references/schema-drift-recovery.md` | When a query fails on a missing or changed column |
| `references/storage-layout.md` | When looking up the full file/DB path table |
| `references/receipt-line-items-insert.md` | Before inserting parsed receipt line items |
| `references/receipt-parsing.md` | When building or debugging the receipt parser |
| `references/backfill-linkage.md` | When linking historical unlinked transactions |
| `references/provenance.md` | When auditing provenance across the pipeline |
| `references/data-flow.md` | When tracing a record end-to-end |
| `references/merchant_name_geolocation.md` | When deriving locale/geo hints from names |
| `references/verify-bank-alert.md` | When cross-checking a bank alert against the ledger |
| `references/migration-recovery.md` | When the data or secrets directory has moved |
| `references/self_update.md` | When a skill sync hits a dirty tree |
| `references/historical-incidents.md` | When reconstructing a dated pre-2026-08 failure |
| `references/scripts.md` | Quick index of every script here |
| `scripts/styx_common.py` | Importing shared helpers |
| `scripts/seed.py` | Running the seeding/linkage step |
| `scripts/styx_universal_enrich.py` | Running enrichment for all categories |
| `scripts/query.py` | Querying the Styx database |
| `tests/test_styx_common.py` | Before changing a helper |
| `tests/validate_skill.py` | After editing SKILL.md or references |

## Automation

Styx has no in-skill self-update; `skills:update-fleet` (daily 03:15) owns
updates. Recovery when a sync hits a dirty tree: `references/self_update.md`.

## Files

Full file and DB path table: `references/storage-layout.md`.

## OKRs

- **schedule_adherence** — on-demand enrichment completes within 5 minutes
- **data_integrity** — zero raw transaction records modified or deleted

## Visibility

public
