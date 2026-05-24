#!/usr/bin/env python3
"""Incremental transaction sync using Plaid's /transactions/sync endpoint.

Uses cursor-based sync for efficient incremental updates.
Run daily via cron.
"""

import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

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

def store_transaction(conn, tx, account_id):
    """Upsert a single transaction."""
    try:
        conn.execute(
            '''INSERT OR REPLACE INTO transactions
            (account_id, transaction_id, amount, currency, date, authorized_date,
             name, merchant_name, category, category_id, pending, payment_channel,
             personal_finance_category)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (
                account_id,
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
        return True
    except Exception as e:
        print(f"  Error storing tx {tx.get('transaction_id')}: {e}", file=sys.stderr)
        return False

def sync_item(conn, item_id, access_token, institution_name):
    """Sync transactions for a single item using cursor-based pagination."""
    # Get stored cursor
    cursor_row = conn.execute(
        'SELECT cursor FROM sync_cursor WHERE item_id = ?', (item_id,)
    ).fetchone()
    cursor = cursor_row[0] if cursor_row else None

    # Get account IDs for this item
    accounts = conn.execute(
        'SELECT account_id FROM accounts WHERE plaid_item_id = ?', (item_id,)
    ).fetchall()
    account_map = {a[0]: True for a in accounts}

    added = 0
    modified = 0
    removed = 0
    has_more = True

    while has_more:
        payload = {'access_token': access_token}
        if cursor:
            payload['cursor'] = cursor

        result = plaid_post('/transactions/sync', payload)

        if 'error' in result:
            print(f"  {institution_name} ERROR: {result['error']}", file=sys.stderr)
            return added, modified, removed

        # Process added
        for tx in result.get('added', []):
            if tx.get('account_id') in account_map:
                if store_transaction(conn, tx, tx['account_id']):
                    added += 1

        # Process modified
        for tx in result.get('modified', []):
            if tx.get('account_id') in account_map:
                if store_transaction(conn, tx, tx['account_id']):
                    modified += 1

        # Process removed
        for tx in result.get('removed', []):
            tx_id = tx.get('transaction_id')
            if tx_id:
                conn.execute('DELETE FROM transactions WHERE transaction_id = ?', (tx_id,))
                removed += 1

        cursor = result.get('next_cursor')
        has_more = result.get('has_more', False)

        # Update cursor after each page
        conn.execute(
            'INSERT OR REPLACE INTO sync_cursor (item_id, cursor, updated_at) VALUES (?, ?, datetime("now"))',
            (item_id, cursor)
        )
        conn.commit()

        if has_more:
            time.sleep(0.3)

    return added, modified, removed

def main():
    conn = sqlite3.connect(DB_PATH)

    items = conn.execute(
        'SELECT id, access_token, item_id, institution_name FROM plaid_items'
    ).fetchall()

    total_added = 0
    total_modified = 0
    total_removed = 0

    print(f"Incremental sync started at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}")

    for item_id, access_token, plaid_item_id, inst_name in items:
        print(f"\n{inst_name}...")
        try:
            added, modified, removed = sync_item(conn, plaid_item_id, access_token, inst_name)
            print(f"  +{added} added, ~{modified} modified, -{removed} removed")
            total_added += added
            total_modified += modified
            total_removed += removed
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)

    print(f"\n{'='*60}")
    print(f"Sync complete: +{total_added} added, ~{total_modified} modified, -{total_removed} removed")

    # Update account balances
    print(f"\nUpdating balances...")
    for item_id, access_token, plaid_item_id, inst_name in items:
        result = plaid_post('/accounts/balance/get', {'access_token': access_token})
        if 'accounts' in result:
            for acct in result['accounts']:
                conn.execute(
                    '''UPDATE accounts SET
                        current_balance = ?,
                        available_balance = ?
                    WHERE account_id = ?''',
                    (acct.get('balances', {}).get('current'),
                     acct.get('balances', {}).get('available'),
                     acct['account_id'])
                )
    conn.commit()

    total_tx = conn.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]
    print(f"Total transactions in DB: {total_tx}")

    conn.close()

if __name__ == '__main__':
    main()
