# The weekly drift check: `vendor/em/` against Execution Market

`.github/workflows/em-drift.yml` runs
`scripts/vendor_em.py --check --public-log` every Monday at 07:23 UTC, and on
demand (Actions → em-drift → Run workflow), against a fresh clone of
Execution Market's `main`. Before it, the check ran only when someone ran it
by hand: when EM changed its networks, enums or retry table, EM's
`sdk-tests` job turned red and this repository stayed green.

## One-time setup (someone who administers both repositories)

EM is private, and the run reads it with a **read-only deploy key**, kept in
the `em-drift` environment of this repository. Until the secret exists, every
run is red with `Secret EM_DEPLOY_KEY of environment em-drift is not set`.
That is on purpose: a check that did not run must not read as green. The
commands assume a POSIX shell.

1. **Before the workflow first runs on `main`** (so, before it is merged):
   in this repository → Settings → Environments → New environment `em-drift`
   → Deployment branches and tags → Selected branches and tags → add `main`,
   ref type Branch (as the `pypi` environment of `publish.yml` is set up).
   A run against a missing environment creates it on the fly, with no branch
   rule, and a secret set in it afterwards would reach any branch. Check:
   `gh api repos/UltravioletaDAO/uvd-em-sdk-python/environments/em-drift/deployment-branch-policies --jq '.branch_policies[] | [.name, .type]'`
   prints `["main","branch"]` and nothing else.
2. `ssh-keygen -t ed25519 -N '' -C 'uvd-em-sdk-python em-drift' -f em_drift`
3. In `UltravioletaDAO/execution-market` → Settings → Deploy keys → Add deploy
   key: paste `em_drift.pub` and leave **Allow write access** unchecked.
   (`gh repo deploy-key add em_drift.pub -R UltravioletaDAO/execution-market`
   does the same, but GitHub deletes a key that `gh` added if `gh`'s token is
   revoked.)
4. `gh secret set EM_DEPLOY_KEY --env em-drift -R UltravioletaDAO/uvd-em-sdk-python < em_drift`.
   Put it in the environment only. A repository secret of the same name
   would reach every workflow and every branch of this repository.
5. `rm em_drift em_drift.pub`, then
   `gh workflow run em-drift.yml -R UltravioletaDAO/uvd-em-sdk-python`.

With the secret in an environment limited to `main`, only a job that runs on
`main` and names `environment: em-drift` reads the key. A PR, or another
branch dispatched by hand, is refused before its job starts. So the key is
exactly as safe as `main`: whoever can change `main` can write a job that
reads it.

## Reading a run

| The run says | Meaning | What to do |
|---|---|---|
| two `OK — …` lines | `vendor/em/` is SOURCE's commit, and EM's `main` yields the same bytes | nothing |
| `UPSTREAM DRIFT — origin/main: a re-vendor would change [...]` | EM changed what the SDK mirrors | on a clone, `--check` names the commit; re-vendor it (`--commit <sha>`), then `sync_networks.py`, `sync_enums.py`, `ruff format .`, and a PR. The parity tests name what the SDK must follow |
| `DRIFT — vendor/em/ is not what SOURCE's commit yields: [...]` | `vendor/em/` is not what its own pin yields | re-vendor at the same pin (the `Fix:` line) |
| `STALE — origin/main does not contain SOURCE's commit` | SOURCE pins a commit that is not on EM's `main` (history rewritten, or a pin from another branch) | re-vendor from `main` |
| `ERROR — the check did not finish (<exception>)` | the check failed; its message is left out because it may quote EM | run `--check` on a clone to see it |
| `Secret EM_DEPLOY_KEY of environment em-drift is not set` | the setup above is missing | do the setup |
| `The clone of Execution Market's main failed (Permission denied (publickey))` or `(Repository not found)` | the deploy key was removed, or EM moved | replace the key (steps 2, 3, 4) |
| `… failed (Host key verification failed)` | GitHub's SSH host key is not the one pinned in the workflow | compare `HOST_KEY` with https://api.github.com/meta |
| `… failed (unknown cause)` | git's own words are left out of the log | reproduce the clone with the key, off this log |

## What the log can say

This repository is public, and so is every Actions log. The workflow
therefore:

- keeps the key in an environment that only a job on `main` can use, and
  has no `pull_request` trigger: a fork's PR never runs this job;
- clones EM by hand, quietly: `main` only, without tags or blobs (`git show`
  fetches the few files the check reads). `actions/checkout` prints the commit
  it checks out and, with the `fetch-depth: 0` the check needs (SOURCE's
  commit and its ancestry), the name of every branch and tag of EM;
- names a failed clone with a fixed line, never with git's or ssh's words;
- prints through `--public-log`: the vendored file names and SOURCE's commit,
  both already public here, and nothing it learnt from the clone (not the
  newer commit, its date, nor an error message, since a `SyntaxError` quotes
  the EM line it failed on);
- keeps no cache and uploads no artifact. The key and the clone live in
  `$RUNNER_TEMP`, which the runner empties at the end of the job.

`tests/test_vendor_em.py` checks each of these, and runs the scripts of both
steps against a fake EM.

## Also

- GitHub disables a scheduled workflow of a public repository after 60 days
  without activity in it. Re-enable it in Actions → em-drift.
- Failed scheduled runs notify the user who created the workflow; once someone
  else edits its `cron` line, that user; after a disable and re-enable,
  whoever re-enabled it.
- To read the last verdict without opening the log:
  `gh run list -R UltravioletaDAO/uvd-em-sdk-python -w em-drift.yml -L 1 --json conclusion,createdAt,url`.
- The job runs only in this repository, which it recognises by id
  (`gh api repos/UltravioletaDAO/uvd-em-sdk-python --jq .id`), so that it
  still runs after a rename.
- `scripts/upstream-check.plist.template` is the same check scheduled on a
  maintainer's machine, through that machine's own access to EM: no deploy
  key, and a log that stays on the machine.
