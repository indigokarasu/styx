---
name: ocas-styx
description: 'Styx: transaction data store with merchant enrichment. Provides a clean,
  queryable interface over raw bank transaction data. Enriches garbled/ obfuscated
  transaction names into real business entities using SearXNG search + LLM resolution.
  Includes financial sync (Plaid API) for pulling transactions and balances daily.
  Other skills (Taste, Rally, Vesper, Corvus) read from Styx for consumption signals,
  spending analysis, and pattern detection. Styx is append-only: raw transaction data
  is never modified. Enrichment adds resolved merchant records and links transactions
  to canonical business entities. Trigger phrases: ''styx'', ''transaction data'',
  ''merchant lookup'', ''what did I spend'', ''enrich transactions'', ''business matching'',
  ''where did I spend'', ''pull transactions'', ''sync my bank'', ''bank API'', ''financial
  data'', ''account balance''.

  '
metadata:
  author: Indigo Karasu
  version: 1.1.0
license: MIT
---

# Styx — Transaction Data Store

Styx is the system's transaction intelligence layer. It sits between raw bank
data (from Plaid via financial-sync) and consumer skills that need clean
merchant information (Taste, Rally, Vesper, Corvus, Sands).

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

```sql
-- Canonical business/merchant entities
CREATE TABLE merchants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,              -- canonical business name
    normalized_name TEXT NOT NULL,   -- lowercase, stripped for matching
    category TEXT,                   -- business category (restaurant, grocery, etc.)
    subcategory TEXT,                -- finer classification
    address TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    phone TEXT,
    website TEXT,
    source TEXT,                     -- how this merchant was identified
    confidence REAL,                 -- 0.0-1.0 confidence in this entity
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(normalized_name)
);

-- Links transactions to merchants (many-to-many)
CREATE TABLE transaction_merchants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,    -- from transactions.db
    merchant_id INTEGER NOT NULL,
    raw_name TEXT NOT NULL,          -- original transaction name
    match_method TEXT,               -- 'exact', 'fuzzy', 'search', 'llm'
    confidence REAL,                 -- 0.0-1.0
    is_primary INTEGER DEFAULT 1,    -- best match for this transaction
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (merchant_id) REFERENCES merchants(id),
    UNIQUE(transaction_id, merchant_id)
);

-- Enrichment run tracking
CREATE TABLE enrichment_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    transactions_processed INTEGER DEFAULT 0,
    merchants_found INTEGER DEFAULT 0,
    merchants_created INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',   -- 'running', 'completed', 'failed'
    error TEXT
);
```

## Enrichment pipeline

The pipeline resolves garbled/obfuscated transaction names into real businesses.

### When to enrich

1. Initial load — after first Plaid historical pull
2. After each incremental sync (new transactions)
3. On-demand — when a consumer skill needs a specific merchant resolved

### Pipeline stages

```
Stage 1: EXACT MATCH
  → Check if merchant_name or name already matches a known merchant
  → If yes, create link with confidence 1.0, skip remaining stages

Stage 2: FUZZY MATCH
  → Normalize the transaction name (strip prefixes like ABM-, TCB*, MED*, DD *)
  → Fuzzy match against existing merchants table
  → If similarity > 0.85, create link with confidence 0.9

Stage 3: SEARCH (SearXNG)
  → Query SearXNG with cleaned name + "San Francisco" (user's city)
  → Parse results for business name, address, category
  → If found, create merchant + link with confidence 0.8

Stage 4: LLM RESOLUTION
  → For remaining unresolved transactions, use LLM to identify the business
  → Input: transaction name, amount, category, personal_finance_category
  → Output: business name, category, confidence
  → Create merchant + link with LLM-assessed confidence

Stage 5: MANUAL REVIEW QUEUE
  → Transactions with confidence < 0.5 go to review queue
  → File: {agent_root}/data/styx/review_queue.jsonl
  → User can review and confirm/reject
```

### Name cleaning rules

Common patterns in Plaid transaction names:

| Pattern | Type | Cleaning |
|---------|------|----------|
| `ABM-350 MISSION GARAGE` | Prefix | Strip `ABM-` prefix |
| `TCB*MTA METER MTA P` | Prefix | Strip `TCB*` prefix |
| `MED*UCSF HEALTH` | Prefix | Strip `MED*` prefix |
| `DD *DOORDASH ROYALINDI` | Prefix | Strip `DD *DOORDASH ` prefix |
| `FSP*ALVES CLEANING SF` | Prefix | Strip `FSP*` prefix |
| `ABC*BAKAR FITNESS CENT` | Prefix | Strip `ABC*` prefix |
| `TST*TARTINE MANUFACTOR` | Prefix | Strip `TST*` prefix |
| `***************` | Redacted | Skip, no enrichment possible |
| `eBay O***-*****-*****` | Redacted | Use base name (eBay) |
| `UNITED **************` | Redacted | Use base name (United) |
| `UBER *ONE MEMBERSHIP` | Asterisk | Strip after ` *` for matching |

## Query API

Other skills read from Styx using these patterns:

### Get enriched transactions for a category

```python
import sqlite3
conn = sqlite3.connect('{agent_root}/data/styx.db')
# Get all restaurant transactions with resolved merchant names
rows = conn.execute('''
    SELECT t.name as raw_name, t.amount, t.date,
           m.name as merchant_name, m.category, m.city,
           tm.confidence, tm.match_method
    FROM transaction_merchants tm
    JOIN merchants m ON tm.merchant_id = m.id
    JOIN transactions t ON tm.transaction_id = t.transaction_id
    WHERE m.category = 'restaurant'
    ORDER BY t.date DESC
''').fetchall()
```

### Get spending by merchant

```python
rows = conn.execute('''
    SELECT m.name, SUM(t.amount) as total, COUNT(*) as visits
    FROM transaction_merchants tm
    JOIN merchants m ON tm.merchant_id = m.id
    JOIN transactions t ON tm.transaction_id = t.transaction_id
    WHERE tm.is_primary = 1
    GROUP BY m.id
    ORDER BY total DESC
''').fetchall()
```

### Get unresolved transactions (for enrichment)

```python
rows = conn.execute('''
    SELECT t.transaction_id, t.name, t.merchant_name, t.amount,
           t.date, t.personal_finance_category
    FROM transactions t
    LEFT JOIN transaction_merchants tm ON t.transaction_id = tm.transaction_id
    WHERE tm.id IS NULL
    ORDER BY t.date DESC
''').fetchall()
```

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

## Support File Map

| File | When to read |
|---|---|
| `references/financial-sync.md` | Before configuring Plaid sync; contains provider setup, credentials, and cron configuration |
| `references/scripts.md` | Before running enrichment or query scripts; contains CLI usage and known fixes |

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
