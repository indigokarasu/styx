# Styx Self-Update Procedure

## When to run

- After a new version is pushed to the GitHub source
- When scripts or schema definitions need updating
- Periodic refresh (no fixed schedule — run on demand)

## Procedure

1. Pull the latest skill package from GitHub:
   ```bash
   cd {skill_root} && git pull origin main
   ```

2. Check for schema migrations in `references/schema.md`. If the schema version
   has changed, run the migration script:
   ```bash
   python3 {skill_root}/scripts/seed.py --migrate
   ```

3. Verify the database is intact:
   ```bash
   python3 {skill_root}/scripts/query.py --health-check
   ```

4. Review `references/scripts.md` for any new or changed script flags.

## What updates do NOT touch

- `{agent_root}/data/styx.db` — enriched merchant data is never modified by updates
- `{agent_root}/data/transactions.db` — raw Plaid data is never modified by updates
- `{agent_root}/data/styx/review_queue.jsonl` — manual review queue is preserved
- `{agent_root}/data/banksync.md` — bank sync state is preserved

## Rollback

If an update causes issues:
```bash
cd {skill_root} && git reset --hard HEAD~1
```
