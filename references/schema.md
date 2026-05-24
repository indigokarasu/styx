# Styx Database Schema

Styx maintains its own SQLite database at `{agent_root}/data/styx.db`.

## Tables

### merchants

Canonical business/merchant entities.

```sql
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
```

### transaction_merchants

Links transactions to merchants (many-to-many).

```sql
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
```

### enrichment_runs

Tracks enrichment pipeline execution.

```sql
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
