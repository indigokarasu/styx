# Self-Update

## How Styx updates itself

**It does not.** In-skill self-update was removed in commit `abe1863` and
centralized into the `skills:update-fleet` cron (daily 03:15, id
`a254fe53d548`), which syncs every skill in the fleet. The retired
`styx:update` job is disabled and its wrapper `~/.hermes/scripts/update_styx.sh`
is no longer invoked.

So: **do not run a self-update from inside this skill.** If you are here
because a task told you to "update Styx", the correct action is to let
`skills:update-fleet` run, or to report that no in-skill update path exists.
`references/self_update.md` is retained for the git-level recovery notes below,
which are still needed when the centralized sync hits a dirty tree.

## Getting the latest version

```bash
cd ~/.hermes/profiles/indigo/skills/ocas-styx
git pull origin main
```

Repo: `https://github.com/indigokarasu/styx.git` (branch `main`). If you are
reading this in a published clone, the same command works — the subdirectory is
the skill.

## When a sync fails: untracked files block `git pull`

`git stash` only stashes **tracked** files. Any new file in the skill directory
is untracked, and a merge that would touch the same path refuses to proceed.
The sync then reports a dirty tree and gives up.

```bash
# 1. See what is actually untracked
git status --porcelain

# 2. Move untracked files aside (pick your own scratch location)
mkdir -p /tmp/styx-untracked
git ls-files --others --exclude-standard -z \
  | tar --null -T - -cf - | tar -xf - -C /tmp/styx-untracked

# 3. Now the pull will apply
git pull origin main

# 4. Compare before restoring anything
diff -r /tmp/styx-untracked . 
```

Never reach for `git clean -fd` here. It deletes untracked work with no undo,
and the whole point of moving files aside is to keep that work recoverable.

## When `git stash pop` conflicts

If a pull landed changes on the same lines as stashed work, `git stash pop`
stops with conflict markers. Resolve by hand — do not `git checkout -- .` to
break the tie, that discards the stash as well as the conflict.

To see what is still stashed:

```bash
git stash list
```

## After any pull

```bash
python3 -m unittest discover -s tests    # 22 tests, must be OK
python3 scripts/styx_universal_enrich.py --help    # exit 0, no side effects
```

If the tests fail after a sync, the sync brought in a regression. Report it
rather than relaxing the test.
