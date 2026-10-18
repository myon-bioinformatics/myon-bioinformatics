# Parent-owned patch application

For an explicitly requested coordinated change, use Git's existing mbox format
and `git am --3way`. Do not add a GHI write command, a second vendor lock schema,
a scheduled updater, or an automatic promotion workflow.

`scripts/apply_repo_patches.py` is a stdlib-only local orchestrator. Supply each
reviewed checkout/mailbox pair explicitly. It does not discover repositories
from the vendor registry: registration is not authorization to modify a repo,
and archived or out-of-scope consumers must not be pulled into a rollout.

## Prepare and plan

Prepare **one repository-specific mailbox per target** from its reviewed base,
including only that repository's intended changes. A patch built for one tree
is not universally applicable to unrelated repositories.

```sh
# In each preparation branch; BASE is the reviewed full commit SHA.
git format-patch --stdout --full-index --binary BASE..HEAD > ../consumer.mbox

# From the parent checkout; repeat --target for the explicit approved set.
python -S scripts/apply_repo_patches.py \
  --branch chore/shared-pins-20261014 \
  --worktree-root ../rollout-worktrees \
  --target ../checkouts/cli_args ../patches/cli_args.mbox \
  --target ../checkouts/gh_identity ../patches/gh_identity.mbox
```

Without `--apply` this checks local preconditions and prints the captured base
SHAs. It does **not** prove that a mailbox will apply. Prepare/fetch sufficient
history separately: three-way fallback requires the patch's original blobs to
be available locally. This script never fetches, resolves a moving upstream
baseline, or claims that a local checkout is current with remote main.

## Apply

Run the same command with `--apply`. All targets are preflighted before any
branch/worktree is created. A new feature branch and a linked worktree are
created for each target, based on its captured SHA; then Git runs
`git am --3way -- <mailbox>`. Original checkouts and their main branches are not
switched or edited. Dirty sources, duplicate targets, existing output paths,
existing branches, invalid branch names and unfinished Git operations fail.
Inherited `GIT_*` routing variables are removed to avoid accidentally selecting
another index/worktree. Local Git configuration, hooks, filters, and the
explicitly supplied patches remain trusted; this is not an execution sandbox.
Do not run other writers against the rollout worktrees or patch files.

On the first error the batch stops. The failed worktree is retained for
inspection; Git's conflict diagnostics are reported. No `--skip`, forced
ours/theirs selection, automatic abort, reset, stash, push, PR, or merge occurs.
Earlier successful targets remain applied locally: the batch is not an atomic
cross-repository transaction. Inspect the printed worktree and use ordinary
`git am --continue` after review, or `git am --abort` to abandon that worktree's
application. A retry does not silently overwrite the existing branch/worktree.

Exit 0 means a valid plan, or all requested local applications completed when
`--apply` was supplied. Exit 1 means a preflight/Git/I/O failure; CLI usage
errors exit 2. `APPLIED` is **not** a test, CI, or merge-success assertion.

## Validate before publishing

Run each repository's existing tests and the parent's pinned
`vendor_sync.py check --root <worktree> --manifest <existing-lock-path>` where
applicable. Vendor identity remains solely in the existing `vendor-lock/1`,
with verified source/license bytes, blob SHA and SHA-256; this helper neither
creates nor rewrites locks. Review the actual patch and compare the destination
branch with the recorded base before publishing PRs. Check same-head CI and
reviews before any merge; a successful `git am` does not replace those gates.

For the 2026-10-14 follow-up tracked in parent Issue #56, preserve the agreed
JUnit pin `652608b16c73ecd1e39db848abba3d08a00a8a3a` and `commit-sha`, and the
vendor-sync pin `f907560c2b9b3f7a0f78f4434f547f91cb2ef954`. Workflow pin cleanup
and lock enrollment for cli_args, gh_identity, and github.io are separate
reviewed patch contents. BlueProbe vendor enrollment moved to the Aoi subtree
model (Aoi `child-heads.json`); do not re-register it in the parent. Do not infer a 16-repository target set from
an older inventory or treat this runner's implementation as completion of that
rollout. This change does not re-enable vendor auto-update jobs.

## Regression tests

```sh
python -m pytest -q tests/test_apply_repo_patches.py
```

Tests use real disposable local Git repositories, including a patch rejected
by ordinary `git apply --check` that succeeds with three-way fallback, a real
conflict, batch preflight/fail-stop behavior, non-mutation of main, repeat-run
protection, malformed input, and paths containing spaces/Japanese text. The
existing parent Python test workflow collects these tests; no new updater or
workflow permissions are required.
