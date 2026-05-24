#!/usr/bin/env python3
"""Seed the Styx merchants table from known-clean transaction data.

This bootstranks the enrichment pipeline by:
1. Extracting all unique merchant_name values from transactions
2. Creating merchant records for each unique name
3. Linking transactions that already have clean merchant_names

Run this before the main enrichment pipeline.
"""

import json
import sqlite3
import sys

STYX_DB = '/root/.hermes/data/styx.db'
TXN_DB = '/root/.hermes/data/transactions.db'

CATEGORY_MAP = {
    'FOOD_AND_DRINK': 'restaurant',
    'GENERAL_MERCHANDISE': 'retail',
    'TRANSPORTATION': 'transport',
    'GENERAL_SERVICES': 'service',
    'ENTERTAINMENT': 'entertainment',
    'MEDICAL': 'medical',
    'HOME_IMPROVEMENT': 'home',
    'LOAN_PAYMENTS': 'finance',
    'PERSONAL_CARE': 'personal_care',
    'TRANSFER_OUT': 'transfer',
    'INCOME': 'income',
    'BANK_FEES': 'finance',
    'GOVERNMENT_AND_NON_PROFIT': 'government',
    'TRANSFER_IN': 'transfer',
    'RENT_AND_UTILITIES': 'housing',
    'LOAN_DISBURSEMENTS': 'finance',
    'TRAVEL': 'travel',
}

def normalize(name):
    if not name:
        return ''
    n = name.lower()
    import re
    n = re.sub(r'[^a-z0-9\s]', '', n)
    n = re.sub(r'\s+', ' ', n).strip()
    return n

def main():
    styx_conn = sqlite3.connect(STYX_DB)
    txn_conn = sqlite3.connect(TXN_DB)

    # Ensure tables exist
    styx_conn.execute('''CREATE TABLE IF NOT EXISTS merchants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        normalized_name TEXT NOT NULL,
        category TEXT,
        subcategory TEXT,
        address TEXT,
        city TEXT,
        state TEXT,
        zip TEXT,
        phone TEXT,
        website TEXT,
        source TEXT,
        confidence REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(normalized_name)
    )''')
    styx_conn.execute('''CREATE TABLE IF NOT EXISTS transaction_merchants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id TEXT NOT NULL,
        merchant_id INTEGER NOT NULL,
        raw_name TEXT NOT NULL,
        match_method TEXT,
        confidence REAL,
        is_primary INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (merchant_id) REFERENCES merchants(id),
        UNIQUE(transaction_id, merchant_id)
    )''')
    styx_conn.execute('''CREATE TABLE IF NOT EXISTS enrichment_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TIMESTAMP,
        completed_at TIMESTAMP,
        transactions_processed INTEGER DEFAULT 0,
        merchants_found INTEGER DEFAULT 0,
        merchants_created INTEGER DEFAULT 0,
        status TEXT DEFAULT 'running',
        error TEXT
    )''')
    styx_conn.commit()

    # Get all transactions with their merchant_name and personal_finance_category
    rows = txn_conn.execute('''
        SELECT transaction_id, name, merchant_name, personal_finance_category, amount, date
        FROM transactions
        ORDER BY date DESC
    ''').fetchall()

    # Phase 1: Create merchants from clean merchant_name values
    merchants_created = 0
    merchants_skipped = 0

    # Collect unique merchant names
    unique_merchants = {}
    for txn_id, name, merchant_name, pfc, amount, date in rows:
        if merchant_name and merchant_name.strip() and merchant_name != 'None':
            clean = merchant_name.strip()
            norm = normalize(clean)
            if norm and norm not in unique_merchants:
                cat = CATEGORY_MAP.get(pfc, 'other')
                unique_merchants[norm] = (clean, cat)

    print(f"Found {len(unique_merchants)} unique merchant names")

    for norm, (name, cat) in unique_merchants.items():
        try:
            styx_conn.execute(
                'INSERT OR IGNORE INTO merchants (name, normalized_name, category, source, confidence) VALUES (?, ?, ?, ?, ?)',
                (name, norm, cat, 'plaid_merchant_name', 1.0)
            )
            if styx_conn.total_changes:
                merchants_created += 1
            else:
                merchants_skipped += 1
        except Exception as e:
            print(f"  Error creating merchant '{name}': {e}")
            merchants_skipped += 1

    styx_conn.commit()
    print(f"Merchants created: {merchants_created}, skipped (duplicates): {merchants_skipped}")

    # Phase 2: Link transactions that have clean merchant_names
    links_created = 0
    for txn_id, name, merchant_name, pfc, amount, date in rows:
        if not merchant_name or not merchant_name.strip() or merchant_name == 'None':
            continue
        norm = normalize(merchant_name.strip())
        row = styx_conn.execute(
            'SELECT id FROM merchants WHERE normalized_name = ?', (norm,)
        ).fetchone()
        if row:
            try:
                styx_conn.execute(
                    '''INSERT OR IGNORE INTO transaction_merchants
                    (transaction_id, merchant_id, raw_name, match_method, confidence, is_primary)
                    VALUES (?, ?, ?, ?, ?, 1)''',
                    (txn_id, row[0], name or merchant_name, 'exact', 1.0)
                )
                links_created += 1
            except Exception as e:
                pass

    styx_conn.commit()
    print(f"Transaction-merchant links created: {links_created}")

    # Summary
    total_merchants = styx_conn.execute('SELECT COUNT(*) FROM merchants').fetchone()[0]
    total_links = styx_conn.execute('SELECT COUNT(*) FROM transaction_merchants').fetchone()[0]
    total_txns = txn_conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
    unlinked = total_txns - total_links

    print(f"\n{'='*60}")
    print(f"Seeding complete:")
    print(f"  Total transactions: {total_txns}")
    print(f"  Merchants: {total_merchants}")
    print(f"  Linked: {total_links}")
    print(f"  Unlinked (need enrichment): {unlinked}")

    # Show unlinked count by category
    print(f"\nUnlinked transactions by category:")
    styx_conn.execute(f'ATTACH DATABASE "{TXN_DB}" AS txndb')
    rows = styx_conn.execute('''
        SELECT t.personal_finance_category, COUNT(*) as cnt
        FROM txndb.transactions t
        LEFT JOIN transaction_merchants tm ON t.transaction_id = tm.transaction_id
        WHERE tm.id IS NULL
        GROUP BY t.personal_finance_category
        ORDER BY cnt DESC
    ''').fetchall()
    for cat, cnt in rows:
        print(f"  {cat or 'Unknown'}: {cnt}")

    styx_conn.close()
    txn_conn.close()

if __name__ == '__main__':
    main()
