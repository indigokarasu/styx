---
name: ocas-styx
description: 'Transaction data store with merchant enrichment. Provides a clean, queryable interface over raw bank transaction data. Enriches garbled/obfuscated transaction names into real business entities using SearXNG search plus LLM resolution. Includes financial sync (Plaid API) for pulling transactions and balances daily. Other skills (Taste, Rally, Vesper, Corvus, Sands) read from Styx for consumption signals, spending analysis, and pattern detection. NOT for creating transactions (use bank), budgeting strategy (use Rally), or email-based consumption scanning (use Taste).'
license: MIT
source: https://github.com/indigokarasu/styx
includes:
- references/**
- scripts/**
metadata:
  author: Indigo Karasu (indigokarasu)
  version: 1.4.0
tags:
- transactions
- finance
- merchant-enrichment
- banking
- data-store
triggers:
- transaction data
- bank transactions
- merchant enrichment
- financial data store
- query transactions
---

# Styx — Transaction Data Store

Styx is the system's transaction intelligence layer. It sits between raw bank
data (from Plaid via financial-sync) and consumer skills that need clean
merchant information (Taste, Rally, Vesper, Corvus, Sands).

## When to Use

- Enriching garbled/obfuscated transaction names into real business entities
- Merchant lookup and business matching from transaction data
- Answering "what did I spend" or "where did I spend" questions
- Pulling/syncing bank transactions via Plaid API
- Spending analysis, pattern detection, or calendar-based spending context
- Providing clean merchant data to consumer skills (Taste, Rally, Vesper, Corvus, Sands)
- Parsing email receipts (e.g., Rainbow Grocery eReceipts) and storing line items in `receipt_line_items` table

## When NOT to Use

- Budgeting strategy or financial planning (use Rally)
- Email-based consumption scanning (use Taste)
- Creating or modifying transactions (use your bank directly)
- General web research or non-transaction search (use Sift)
- Account management (adding/removing bank links) — use Plaid Link flow directly

## Core principles

1. **Raw data is sacred** — transaction records from Plaid are never modified.
   Enrichment data lives in separate tables, linked by transaction_id.
2. **Append-only** — Styx only adds new records. It never deletes or updates
   raw transactions. Enrichment records can be superseded (marked stale) but
   not deleted.
3. **Read-only for consumers** — other skills query Styx via the query API
   or read the SQLite DB directly. They do NOT write to Styx tables.
4. **Enrichment is idempotent** — running enrichment on already-enriched
   transactions produces the same result. Safe to re-run.

## Data flow

See `references/data-flow.md` for the data flow diagram.

## Database

Styx maintains its own SQLite database at `/root/.hermes/data/styx.db`.
**IMPORTANT:** Hardcode this path. Do NOT use `{agent_root}` — it resolves to the indigo profile home, not the shared data directory.

The active DBs are:
- `/root/.hermes/data/transactions.db` — raw Plaid transaction data
- `/root/.hermes/data/styx.db` — enriched merchant data

A second copy exists at `/root/.hermes/commons/data/ocas-styx/styx.db` but it is a stale 0-byte stub — ignore it.

### Schema

Three core tables: `merchants`, `transaction_merchants`, `enrichment_runs`.
Receipt parsing table: `receipt_line_items` (23 columns — see below).
Full DDL: [`references/schema.md`](references/schema.md)

### receipt_line_items Table (23 columns)

Used for storing parsed email receipt line items (e.g., Rainbow Grocery eReceipts).

See `references/receipt-line-items-insert.md` for the correct INSERT pattern and gotchas.

## Enrichment pipeline

### Google Places Enrichment (All Categories)

The enrichment pipeline resolves garbled/obfuscated transaction names into real businesses.
The **default script only enriches food merchants**. For full coverage, use the 
**universal enrichment script**:

**Script:** [`styx_universal_enrichment.md`](references/styx_universal_enrichment.md) ← read this reference first

```bash
# Universal enrichment — all non-financial categories
python3 /root/.hermes/commons/data/ocas-styx/styx_universal_enrich.py

# Food-only (original script)
python3 /root/.hermes/skills/ocas-styx/scripts/styx_places_enrich.py --all
```

**Categories covered by universal script:** retail, service, entertainment, transport,
personal_care, medical, home, government, housing, travel, food/restaurant (all 10 food subcategories).

**Categories skipped (no physical location):** transfer, income, bank_fees, loan_payments,
loan_disbursements. These get `source: 'internal'`.

### Legacy LLM Enrichment Pipeline

For garbled names that Google Places can't resolve: exact match → fuzzy match → SearXNG search → LLM resolution → manual review queue. Full details: [`references/enrichment-pipeline.md`](references/enrichment-pipeline.md)

## Query API

Other skills read from Styx using these patterns:
- **Category transactions**: enriched transactions filtered by merchant category
- **Spending by merchant**: aggregated totals and visit counts
- **Unresolved transactions**: candidates needing enrichment

DB path: `{agent_root}/data/styx.db`

## Receipt Parsing Pipeline

When parsing email receipts (e.g., Rainbow Grocery):
1. **Fetch emails** via `get_gmail_messages_content_batch` — large results persisted to `/tmp/hermes-results/<uuid>.txt`
2. **Parse persisted files** — XML wrapper around JSON requires brace-depth counting to extract first complete JSON object
3. **Extract bodies** — split by `\n\nMessage ID: `, then extract between `--- BODY ---` and `---\n\n`
4. **Parse line items** — handle department headers, PLU/UPC codes, prices, weight/quantity info
5. **Write to Styx** — use the `receipt_line_items` INSERT pattern above (22 values, `id` auto-increments)

## Consumer skill contracts

### Taste
Taste reads from Styx to discover restaurants and food businesses that Jared
has transacted with but that didn't appear in email/calendar.

Taste queries:
- `m.category IN ('restaurant', 'cafe', 'bar', 'food')` for dining
- `m.category IN ('grocery', 'supermarket', 'food_store')` for food shopping
- Transactions with `personal_finance_category = 'FOOD_AND_DRINK'` as fallback

Taste does NOT write to Styx. It writes to its own `signals.jsonl` and `items.jsonl`.

### Rally
Rally reads from Styx for spending analysis and budget tracking.

### Vesper
Vesper reads from Styx for daily/weekly spending summaries in briefings.

### Corvus
Corvus reads from Styx for pattern detection in spending behavior.

### Sands
Sands reads from Styx for calendar-based spending context.

## Security
- Styx DB is read-only for consumer skills (enforced by skill contract, not filesystem)
- Raw transaction data in transactions.db is never modified by Styx
- Enrichment data is additive only

## Financial Sync
- Sync script: `{skill_root}/scripts/plaid_sync.py` (incremental, daily 7 AM cron)
- History script: `{skill_root}/scripts/plaid_history.py` (full 24-month pull)
- DB: `{agent_root}/data/transactions.db` (raw, read-only)
- Cron job `a418e00ee21e`: daily 7 AM, `no_agent: true`

## Gotchas

- **Self-update: untracked files block `git pull`** — `git stash` only stashes tracked files. New (untracked) files in the skill directory will block the merge. Move them aside before pulling, then compare/restore afterward.
- **Self-update: stash pop may conflict** — After pulling, `git stash pop` can produce merge conflicts if both the pulled changes and the stashed changes touch the same lines.
- **`query.py --health-check` does not exist** — Use inline Python to verify DB integrity instead.
- **Raw transaction data is sacred** — Styx never modifies or deletes records in `transactions.db`.
- **Name cleaning is essential** — Plaid transaction names are heavily obfuscated (e.g., `DD *DOORDASH ROYALINDI`, `ABM-350 MISSION GARAGE`). Strip prefixes before matching.
- **Redacted names can't be enriched** — Transactions with fully redacted names (`***************`) are skipped entirely.
- **Consumer skills are read-only** — Taste, Rally, Vesper, Corvus, and Sands query Styx but must never write to Styx tables.
- **receipt_line_items INSERT requires 22 values** — The table has 23 columns but `id` auto-increments.
- **`google_auth_mcp` import path is profile-dependent** — When running under the `indigo` Hermes profile, `Path.home()` returns `/root/.hermes/profiles/indigo/home` instead of `/root`. Scripts that do `sys.path.insert(0, str(Path.home() / '.hermes' / 'scripts'))` or `sys.path.insert(0, str(AGENT_ROOT / 'scripts'))` will fail to find `google_auth_mcp.py`. **Fix:** Hardcode `sys.path.insert(0, str(Path('/root/.hermes/scripts')))` in any script that imports `google_auth_mcp`. **Affected scripts (all fixed as of 2026-06-04):** dispatch: `triage.py`, `check_unread.py`, `gmail_search.py`, `gmail_scan.py`; taste: `email_scan.py`, `run_historical_scans.py`; scripts: `email_check.py`, `dream_journal_pipeline.py`.
- **Indigo's OAuth token file may lack `client_secret`** — The token file at `/root/.google_workspace_mcp/credentials/mx.indigo.karasu@gmail.com.json` may only have `access_token`, `refresh_token`, `client_id` — but `google_auth_mcp.py` needs `client_secret` for token refresh and a `token` key alias. **Fix:** Add `client_secret` from the cached client secret file. Also add `token` as an alias for `access_token` and `token_uri: 'https://oauth2.googleapis.com/token'`.
- **Jared's token refresh adds `access_token` key** — When refreshing Jared's token, the Google OAuth response includes `access_token` (not `token`). The original file used `token` as the key. After refresh, both keys exist. `google_auth_mcp.py` reads `token_data.get("token")`, so ensure the `token` key is present.
- **styx.db may exist with no tables** — The DB file can be created empty (0 bytes) by the skill initialization script without the schema being applied. Before any receipt parsing or enrichment, verify tables exist.
- **`llm_resolve.py` does NOT work in cron/background context** — The script calls `hermes ask --no-stream` via subprocess, which returns no output when there is no interactive session.
- **styx_places_enrich.py is food-only** — The original enrichment script only covers food/restaurant categories. Use `styx_universal_enrich.py` for all categories. See `references/styx_universal_enrichment.md`.

## Post-enrichment verification

After every enrichment run, verify the results before marking the run as complete:
1. Spot-check 5–10 enriched `transaction_merchants` records at random.
2. Confirm the `enrichment_runs` table row for this run shows status `completed`.
3. Verify `review_queue.jsonl` has been updated with any new low-confidence matches.

## Automation

### Self-update
Pull the latest Styx package from GitHub source. Full procedure: `references/self_update.md`.

## Support File Map

| File | When to read |
|---|---|
| `references/styx_universal_enrichment.md` | Before running Google Places enrichment — use this instead of the food-only default |
| `references/financial-sync.md` | Before configuring Plaid sync |
| `references/scripts.md` | Before running enrichment or query scripts |
| `references/schema.md` | Before querying or modifying the database |
| `references/query-api.md` | Before writing consumer queries |
| `references/enrichment-pipeline.md` | Before running or debugging LLM enrichment |
| `references/styx_universal_enrichment.md` | Before running Google Places enrichment (read FIRST) |
| `references/self_update.md` | Before running self-update |
| `references/cron-gotchas.md` | Before debugging cron enrichment failures |

## Files

See `references/storage-layout.md` for the full file table.

## OKRs

### schedule_adherence
- **Target**: On-demand enrichment runs complete within 5 minutes of invocation.

### data_integrity
- **Target**: Zero raw transaction records modified or deleted by enrichment pipeline.

## Visibility

public