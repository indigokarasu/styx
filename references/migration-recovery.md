# Migration Recovery

Read when a Styx query or script cannot find its database or credentials after a
profile or data migration.

## Symptom

Scripts fail to open `~/.hermes/data/styx.db`, or Plaid scripts cannot read
`~/.hermes/secrets/plaid.env`, even though the data clearly exists somewhere.

## Cause

After a profile/data migration the active files can remain under
`~/.hermes.old/` while every script still hardcodes `~/.hermes/`. The scripts
are correct; the paths moved.

## Check first

```bash
ls -la ~/.hermes.old/data/ 2>/dev/null
ls -la ~/.hermes/data/ 2>/dev/null
ls -la ~/.hermes/secrets/plaid.env 2>/dev/null
```

If `~/.hermes.old/data/styx.db` exists and `~/.hermes/data/styx.db` does not,
this is the situation.

## Fix — symlink, do not move

```bash
mkdir -p ~/.hermes/data
ln -sf ~/.hermes.old/data/styx.db        ~/.hermes/data/styx.db
ln -sf ~/.hermes.old/data/transactions.db ~/.hermes/data/transactions.db
ln -sf ~/.hermes.old/secrets              ~/.hermes/secrets
```

Symlink rather than copy or move: the data stays in one place, the old path
keeps working, and there is no window where both copies exist and diverge.

## Verify

```bash
python3 scripts/styx_universal_enrich.py --dry-run
python3 -m unittest discover -s tests
```

`--dry-run` must exit cleanly and report a merchant count without writing.
The test suite must pass — it uses its own temp DB and never touches the real one.

## Why the universal enrichment script is not in the skill

`styx_universal_enrich.py` lives at
`~/.hermes/profiles/indigo/skills/ocas-styx/scripts/styx_universal_enrich.py`.
The copies under `~/.hermes.old/commons/data/ocas-styx/` and
`~/.hermes/commons/data/ocas-styx/` are not current — run the one in this skill.
See `cron-pipeline.md` for the canonical runner.
