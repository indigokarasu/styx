---
name: ocas-styx
source: https://github.com/indigokarasu/styx
description: 'Styx: transaction data store with merchant enrichment. Provides a clean,
  queryable interface over raw bank transaction data. Enriches garbled/obfuscated
  transaction names into real business entities using SearXNG search + LLM resolution.
  Includes financial sync (Plaid API) for pulling transactions and balances daily.
  Other skills (Taste, Rally, Vesper, Corvus, Sands) read from Styx for consumption
  signals, spending analysis, and pattern detection. NOT for creating transactions
  (use bank), budgeting strategy (use Rally), or email-based consumption scanning
  (use Taste).

  '
license: MIT
metadata:
  author: Indigo Karasu (indigokarasu)
  version: 1.3.0
includes:
- references/**
- scripts/**
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

```
Plaid API → financial-sync → transactions.db (raw)
                                    ↓
                              Styx enrichment pipeline
                              (SearXNG + LLM)
                                    ↓
                         styx.db (enriched merchants,
                                  transaction links)
                                    ↓
                    ┌───────────────┼───────────────┐
                    ↓               ↓               ↓
                 Taste          Rally           Vesper
              (restaurants)  (spending)     (briefings)
```

## Database

Styx maintains its own SQLite database at `{agent_root}/data/styx.db`.

### Schema

Three core tables: `merchants`, `transaction_merchants`, `enrichment_runs`.
Receipt parsing table: `receipt_line_items` (23 columns — see below).
Full DDL: [`references/schema.md`](references/schema.md)

### receipt_line_items Table (23 columns)

Used for storing parsed email receipt line items (e.g., Rainbow Grocery eReceipts).

**Correct INSERT pattern:**
```python
styx.execute('''
    INSERT INTO receipt_line_items (
        transaction_id, message_id, receipt_number,
        plu_upc, product_name, brand, category, subcategory,
        department, price, tax_code,
        quantity, unit_price, weight_lb, price_per_lb,
        is_bulk, is_organic, source_receipt_date,
        match_method, match_confidence, merchant_name, created_at
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
''', (tx_id, msg_id, receipt_num, plu, name, brand, cat, subcat,
      dept, price, tax, qty, unit_p, wlb, pplb,
      is_bulk, is_org, date, method, conf, 'Merchant Name'))
```

**Gotcha — "table has 23 columns but N values supplied"**: Omit `id` (auto-increment) but include all other 22 columns. Always list columns explicitly. Use `CURRENT_TIMESTAMP` for `created_at`. `merchant_name` is required.

## Enrichment pipeline

The pipeline resolves garbled/obfuscated transaction names into real businesses through five stages: exact match → fuzzy match → SearXNG search → LLM resolution → manual review queue. Full details, stage descriptions, and name cleaning rules: [`references/enrichment-pipeline.md`](references/enrichment-pipeline.md)

## Query API

Other skills read from Styx using these patterns:

- **Category transactions**: enriched transactions filtered by merchant category
- **Spending by merchant**: aggregated totals and visit counts
- **Unresolved transactions**: candidates needing enrichment

Full Python examples: [`references/query-api.md`](references/query-api.md)

DB path: `{agent_root}/data/styx.db`

## Receipt Parsing Pipeline

When parsing email receipts (e.g., Rainbow Grocery):

1. **Fetch emails** via `get_gmail_messages_content_batch` — large results persisted to `/tmp/hermes-results/<uuid>.txt`
2. **Parse persisted files** — XML wrapper around JSON requires brace-depth counting to extract first complete JSON object
3. **Extract bodies** — split by `\n\nMessage ID: `, then extract between `--- BODY ---` and `---\n\n`
4. **Parse line items** — handle department headers, PLU/UPC codes, prices, weight/quantity info
5. **Write to Styx** — use the `receipt_line_items` INSERT pattern above (22 values, `id` auto-increments)

**Gmail file parsing gotchas:**
- Files in `/tmp/hermes-results/` have `<untrusted_tool_result>` XML wrapper — extract JSON via `{"result":` search + brace counting
- `json.loads(raw_file)` will fail if there's trailing content — usebrace-depth counting
- Small results are inline only (not persisted); large results (~100KB+) are persisted
- Strip `--- ATTACHMENTS ---` and everything after from email bodies
- If intermediate data gets corrupted, re-fetch from Gmail directly — don't try to reconstruct from partial files

## Consumer skill contracts

### Taste

Taste reads from Styx to discover restaurants and food businesses that Jared
has transacted with but that didn't appear in email/calendar (e.g., walk-ins,
cash transactions, small merchants that don't send confirmation emails).

Taste queries:
- `m.category IN ('restaurant', 'cafe', 'bar', 'food')` for dining
- `m.category IN ('grocery', 'supermarket', 'food_store')` for food shopping
- Transactions with `personal_finance_category = 'FOOD_AND_DRINK'` as fallback

Taste does NOT write to Styx. It writes to its own `signals.jsonl` and
`items.jsonl` as usual.

### Rally

Rally reads from Styx for spending analysis and budget tracking.

### Vesper

Vesper reads from Styx for daily/weekly spending summaries in briefings.

### Corvus

Corvus reads from Styx for pattern detection in spending behavior.

### Sands

Sands reads from Styx for calendar-based spending context (what did Jared
spend at places he visited).

## Security

- Styx DB is read-only for consumer skills (enforced by skill contract, not filesystem)
- Raw transaction data in transactions.db is never modified by Styx
- Enrichment data is additive only
- Review queue is the only user-facing output for low-confidence matches

## Financial Sync

Styx ingests raw transactions from Plaid via the financial sync pipeline.
Full provider docs, setup steps, credentials, and scripts: `references/financial-sync.md`

**Quick reference:**
- Plaid Portal (free personal tier): https://portal.plaid.com
- Sync script: `{skill_root}/scripts/plaid_sync.py` (incremental, daily 7 AM cron)
- History script: `{skill_root}/scripts/plaid_history.py` (full 24-month pull)
- DB: `{agent_root}/data/transactions.db` (raw, read-only)
- Cron job `a418e00ee21e`: daily 7 AM, `no_agent: true`
- Connected: Capital One, Chase, Citi, SF Fire Credit Union, Shaka, Wealthfront
- State file: `{agent_root}/data/banksync.md`

## Recovery Behavior

This skill implements the recovery contract from `spec-ocas-recovery.md`.

- **Evidence**: Every enrichment run writes an evidence record, including no-op runs. The `not_activity_reason` field is mandatory when no side effects occur.
- **Gap detection**: Not applicable — on-demand only.
- **Degraded mode**: When Plaid API or SearXNG are unavailable, logs `degraded: <dependency>` and continues with available sources.
- **Log compaction**: Enrichment logs older than 30 days compacted. Last 7 days retained.

## Gotchas

- **Raw transaction data is sacred** — Styx never modifies or deletes records in `transactions.db`. Enrichment data lives in separate tables (`merchants`, `transaction_merchants`) linked by `transaction_id`.
- **Name cleaning is essential** — Plaid transaction names are heavily obfuscated (e.g., `DD *DOORDASH ROYALINDI`, `ABM-350 MISSION GARAGE`). The enrichment pipeline's name cleaning rules must strip prefixes like `ABM-`, `TCB*`, `MED*`, `DD *DOORDASH ` before matching.
- **Redacted names can't be enriched** — Transactions with fully redacted names (`***************`) are skipped entirely. Partially redacted names (e.g., `UNITED **************`) use the base name for matching.
- **Low-confidence matches go to review queue** — Transactions with enrichment confidence < 0.5 are written to `review_queue.jsonl` for manual review, not silently discarded.
- **Consumer skills are read-only** — Taste, Rally, Vesper, Corvus, and Sands query Styx but must never write to Styx tables. Write access is exclusive to the Styx skill.
- **receipt_line_items INSERT requires 22 values** — The table has 23 columns but `id` auto-increments. List all 22 non-id columns explicitly. Use `CURRENT_TIMESTAMP` for `created_at`. `merchant_name` is required.
- **Gmail persisted results need XML stripping** — Files in `/tmp/hermes-results/` wrap JSON in `<untrusted_tool_result>` tags. Use brace-depth counting to extract the first complete JSON object.
- **`llm_resolve.py` does NOT work in cron/background context** — The script calls `hermes ask --no-stream` via subprocess, which returns no output when there is no interactive session (cron jobs, background agents). Transactions that reach Stage 4 (LLM resolution) will be silently written to `review_queue.jsonl` without actual LLM processing. **Workaround:** Run `llm_resolve.py` manually/interactively after a cron enrichment run, or call the LLM directly via the `ask` tool in an interactive session. See `references/cron-gotchas.md`.

## Post-enrichment verification

After every enrichment run, verify the results before marking the run as complete:
1. Spot-check 5–10 enriched `transaction_merchants` records at random: confirm the resolved merchant name is a real business (not a garbled string that slipped through).
2. Confirm the `enrichment_runs` table row for this run shows status `completed` with the correct `records_processed` count.
3. Verify `review_queue.jsonl` has been updated with any new low-confidence matches (confidence < 0.5).

If spot-check records fail validation (garbled names persisted), re-run the failed transactions through the LLM resolution stage before delivering results to consumer skills.

## Automation

### Self-update

Pull the latest Styx package from GitHub source. Full procedure including schema migrations: `references/self_update.md`.

Quick command:
```bash
cd {skill_root} && git pull origin main
```

Data files (`styx.db`, `transactions.db`, `review_queue.jsonl`) are never modified by updates.

## Support File Map

| File | When to read |
|---|---|
| `references/financial-sync.md` | Before configuring Plaid sync; contains provider setup, credentials, and cron configuration |
| `references/scripts.md` | Before running enrichment or query scripts; contains CLI usage and known fixes |
| `references/schema.md` | Before querying or modifying the database; contains full DDL for all tables |
| `references/query-api.md` | Before writing consumer queries; contains Python examples for common patterns |
| `references/enrichment-pipeline.md` | Before running or debugging enrichment; contains stage details and name cleaning rules |
| `references/self_update.md` | Before running self-update; contains pull/install procedure and migration steps |
| `references/cron-gotchas.md` | Before debugging cron enrichment failures; contains known cron/background execution pitfalls |

## Files

| File | Purpose |
|------|---------|
| `{agent_root}/data/styx.db` | Enriched merchant data + transaction links |
| `{agent_root}/data/transactions.db` | Raw Plaid transaction data (read-only) |
| `{agent_root}/data/styx/review_queue.jsonl` | Low-confidence matches for manual review |
| `{agent_root}/data/styx/intents.jsonl` | Enrichment intent log (what triggered each run) |
| `{agent_root}/data/styx/evidence.jsonl` | Evidence records for each enrichment run |
| `{skill_root}/scripts/enrich.py` | Enrichment pipeline |
| `{skill_root}/scripts/query.py` | Query helper |

## OKRs

### schedule_adherence
- **Target**: On-demand enrichment runs complete within 5 minutes of invocation.
- **Measure**: Time from enrichment trigger to `enrichment_runs.completed_at` for status `completed`.
- **Degraded**: If Plaid or SearXNG unavailable, run completes with partial results and logs `degraded:` — still counts as adherent.

### data_integrity
- **Target**: Zero raw transaction records modified or deleted by enrichment pipeline.
- **Measure**: Append-only audit — `transactions.db` row count never decreases; `merchants` and `transaction_merchants` tables only grow.
- **Degraded**: Enrichment records may be marked stale but never deleted; superseded links retain `is_primary = 0`.

## Initialization

On first run:

1. Create `{agent_root}/data/styx.db` with schema
2. Run initial enrichment on all existing transactions
3. Generate review queue for low-confidence matches
4. Log enrichment run stats

## Visibility

public