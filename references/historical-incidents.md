# Historical Incidents

Dated failure narratives extracted from SKILL.md on 2026-09-29 to keep the main
file inside its token budget. **Read this only when reconstructing an older
failure** — none of it describes current behaviour. Nothing here is a rule.

## OAuth token file may lack `client_secret` (2026-06)

The Google OAuth token file at
`~/.hermes/credentials/<account>.json` can contain only `access_token`,
`refresh_token` and `client_id`. `google_auth_mcp.py` needs `client_secret` to
refresh, and expects the token under a `token` key.

**Fix:** add `client_secret` from the cached client secret file, add `token` as
an alias for `access_token`, and set `token_uri: 'https://oauth2.googleapis.com/token'`.

Related: after a token refresh, Google's response adds an `access_token` key
while the original file used `token`. Both keys end up present;
`google_auth_mcp.py` reads `token_data.get("token")`, so that key must survive.

## `google_auth_mcp` import path is profile-dependent (2026-06-04)

Under the `indigo` Hermes profile, `Path.home()` returns the profile home
(`~/.hermes/profiles/indigo/home`), not `/root`. Scripts that did
`sys.path.insert(0, str(Path.home() / '.hermes' / 'scripts'))` or
`sys.path.insert(0, str(AGENT_ROOT / 'scripts'))` could not import
`google_auth_mcp.py`.

**Fix:** hardcode `sys.path.insert(0, '/root/.hermes/scripts')`.

Affected and fixed as of 2026-06-04 — dispatch: `triage.py`, `check_unread.py`,
`gmail_search.py`, `gmail_scan.py`; taste: `email_scan.py`,
`run_historical_scans.py`; shared scripts: `email_check.py`,
`dream_journal_pipeline.py`.

## Self-update: untracked files block `git pull`

`git stash` only stashes tracked files, so any new file in the skill directory
blocks the merge and the sync reports a dirty tree. Move untracked files aside,
pull, then compare and restore. `git stash pop` can conflict with the pulled
changes when both touch the same lines.

Recovery procedure: `self_update.md`.

## Database and secrets path mismatch (migration artifact)

After a profile/data migration the DBs and secrets can remain under
`~/.hermes.old/` while scripts hardcode `~/.hermes/`. Recipe:
`migration-recovery.md`.

## Styx→Taste pipe dried up (root cause 2026-08-21)

`seed.py` was not running as step 0, so enrichment only saw already-linked
merchants while still reporting "Enriched: N" on stale rows. The signal looked
healthy; the pipe was empty. Now documented in `cron-pipeline.md` and in the
Error Handling table.

## Places 429 vs the cron LLM limit (observed 2026-09-27)

61 of 160 items failed with `HTTP Error 429: Too Many Requests` from Google
Places in `taste_full_enrich.py`, while `styx_universal_enrich.py` in the same
run reported no rate errors. The documented cron limitation
(`llm_resolve.py` returning nothing because `hermes ask` needs an interactive
session) is a *different* failure — a 429 is real quota exhaustion, and the
correct response is to slow down or split the batch, not to classify it as the
known no-op.

## Dated session logs

`session-20260625-dispatch-1846-styx.md` holds one enrichment run's status
snapshot. It is a historical record, not a procedure.
