# Cron Enrichment Gotchas

## `llm_resolve.py` fails silently in cron context

**Symptom:** After a cron enrichment run, the log shows "Resolved 0 via LLM" and unresolved transactions pile up in `review_queue.jsonl` even though they could be resolved.

**Cause:** `llm_resolve.py` calls `hermes ask --no-stream` via `subprocess.run()`. In cron/background sessions there is no interactive Hermes session to answer, so the subprocess returns empty stdout. Every transaction gets classified as "unresolved" and written to the review queue.

**Detection:** After a cron enrichment run, check the review queue for duplicate entries (same transaction ID queued multiple times) — this indicates repeated cron runs without LLM resolution ever succeeding.

**Fix — manual LLM pass:** Run the resolution interactively:
```bash
python3 /root/.hermes/skills/ocas-styx/scripts/llm_resolve.py
```
Or process the queue directly using the `ask` tool in an interactive session.

**Fix — accept review queue as fallback:** When SearXNG is down AND running in cron, partially-redacted transactions will reach Stage 4 and land in the review queue. This is correct behavior — the review queue IS the fallback for unresolvable-in-automation transactions.

## Cross-database queries require ATTACH

When querying across `transactions.db` and `styx.db`, you must use `ATTACH DATABASE` — two separate `sqlite3.connect()` calls cannot join across databases in Python:

```python
import sqlite3
styx = sqlite3.connect('/root/.hermes/data/styx.db')
styx.execute("ATTACH DATABASE '/root/.hermes/data/transactions.db' AS txdb")
# Now query: SELECT ... FROM txdb.transactions t JOIN transaction_merchants tm ...
```
