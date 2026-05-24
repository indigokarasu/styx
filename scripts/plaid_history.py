#!/usr/bin/env python3
"""Pull full transaction history for all connected Plaid items."""

import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path

# Load credentials
def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()
    return env

env = load_env('/root/.hermes/secrets/plaid.env')
CLIENT_ID = env['PLAID_CLIENT_ID']
SECRET = env['PLAID_SECRET']
BASE_URL = 'https://production.plaid.com'
DB_PATH = '/root/.hermes/data/transactions.db'

def plaid_post(endpoint, payload):
    payload['client_id'] = CLIENT_ID
    payload['secret'] = SECRET
    data = json.dumps(payload).encode()
    req = urllib.request.Request(BASE_URL + endpoint, data=data,
                                  headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {'error': f'HTTP {e.code}: {body}'}

def pull_transactions(access_token, account_ids, start_date, end_date):
    """Pull all transactions for given accounts using pagination."""
    all_transactions = []
    offset = 0
    page_size = 500  # Plaid max

    while True:
        result = plaid_post('/transactions/get', {
            'access_token': access_token,
            'start_date': start_date,
            'end_date': end_date,
            'options': {
                'account_ids': account_ids,
                'count': page_size,
                'offset': offset,
            }
        })

        if 'error' in result:
            print(f"  ERROR: {result['error']}", file=sys.stderr)
            return all_transactions

        txs = result.get('transactions', [])
        all_transactions.extend(txs)

        total = result.get('total_transactions', 0)
        offset += len(txs)
        print(f"  Fetched {offset}/{total} transactions...")

        if offset >= total or not txs:
            break

        time.sleep(0.5)  # Rate limit courtesy

    return all_transactions

def store_transactions(conn, transactions):
    """Store transactions in DB, skipping duplicates."""
    inserted = 0
    skipped = 0
    for tx in transactions:
        try:
            conn.execute(
                '''INSERT OR IGNORE INTO transactions
                (account_id, transaction_id, amount, currency, date, authorized_date,
                 name, merchant_name, category, category_id, pending, payment_channel,
                 personal_finance_category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    tx.get('account_id'),
                    tx.get('transaction_id'),
                    tx.get('amount'),
                    tx.get('iso_currency_code', 'USD'),
                    tx.get('date'),
                    tx.get('authorized_date'),
                    tx.get('name'),
                    tx.get('merchant_name'),
                    json.dumps(tx.get('category')) if tx.get('category') else None,
                    tx.get('category_id'),
                    1 if tx.get('pending') else 0,
                    tx.get('payment_channel'),
                    tx.get('personal_finance_category', {}).get('primary') if tx.get('personal_finance_category') else None,
                )
            )
            if conn.total_changes:
                inserted += 1
            else:
                skipped += 1
        except Exception as e:
            print(f"  Skipped tx {tx.get('transaction_id')}: {e}", file=sys.stderr)
            skipped += 1
    conn.commit()
    return inserted, skipped

def main():
    conn = sqlite3.connect(DB_PATH)

    # Get all items with their access tokens and accounts
    items = conn.execute(
        'SELECT id, access_token, institution_name FROM plaid_items'
    ).fetchall()

    # Date range: last 24 months
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d')

    print(f"Pulling transactions from {start_date} to {end_date}")
    print(f"{'='*60}")

    grand_total = 0

    for item_id, access_token, inst_name in items:
        # Get accounts for this item
        accounts = conn.execute(
            'SELECT account_id, name, type FROM accounts WHERE plaid_item_id = ?',
            (item_id,)
        ).fetchall()

        if not accounts:
            print(f"\n{inst_name}: No accounts found, skipping")
            continue

        account_ids = [a[0] for a in accounts]
        acct_names = ', '.join(f"{a[1]} ({a[2]})" for a in accounts)
        print(f"\n{inst_name} — {acct_names}")

        txs = pull_transactions(access_token, account_ids, start_date, end_date)

        if txs:
            inserted, skipped = store_transactions(conn, txs)
            print(f"  Stored: {inserted} new, {skipped} duplicates")
            grand_total += inserted
        else:
            print(f"  No transactions returned")

    print(f"\n{'='*60}")
    print(f"Total new transactions stored: {grand_total}")

    # Summary
    print(f"\n{'='*60}")
    print("Transaction counts by institution:")
    rows = conn.execute('''
        SELECT p.institution_name, COUNT(t.id) as cnt
        FROM transactions t
        JOIN accounts a ON t.account_id = a.account_id
        JOIN plaid_items p ON a.plaid_item_id = p.id
        GROUP BY p.institution_name
        ORDER BY cnt DESC
    ''').fetchall()
    for name, cnt in rows:
        print(f"  {name}: {cnt}")

    total = conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
    print(f"\n  TOTAL: {total} transactions in database")

    conn.close()

if __name__ == '__main__':
    main()
