# Canonical GitHub operations

`gh_ops.py` and `tests/test_gh_ops.py` are maintained in this parent repository.
They were transferred from browser-test-kit; consumers must enroll the shared
source and parent LICENSE using `vendor_sync`, rather than maintain forks or
separate copies of the generic operation/parity tests.

```sh
python -S vendor_sync.py check --manifest vendor.lock.json
python -S gh_ops.py --help
python -S gh_ops.py --json pr-observe OWNER/REPO 24
python -S gh_ops.py comments-file comments.json --last 5
```

The tool is stdlib-only. Its GHI dependency is canonically enrolled under
`vendor/gh_identity.py` with a separate license and vendor lock. In a consumer,
the same unmodified source supports an adjacent enrolled `gh_identity.py` (also
compatible with installation as top-level modules). An unavailable enrolled
module is an import error; no package/environment fallback or download occurs.

The CLI, injectable Client/Response transport, return shapes and explicit write
preconditions are preserved by the transfer. Read-only/dry-run operations remain
read-only; this migration does not execute network writes. GHI remains the owner
of SHA comparison and pure checks classification. gh_ops owns the compatibility
output, PR observation/diff, pagination and operation orchestration. Existing
`gh_workflow.py` preflight/run-selection semantics are separate: this transfer
does not replace it with the smaller legacy dispatch operation.

The parent suite contains the former consumer unit tests plus #48/#49's useful
coverage. It tests missing PR heads as unknown, SHA normalization/mismatch,
PR fields, success/skipped, all-skipped, all-neutral, failure/cancellation,
annotations, and rejection of min_checks=0. Consumer tests should instead prove
locked source identity, CLI/import compatibility, and actual staging/packaging.

Parent PR #52 uses the same `vendor/gh_identity.py`/license destinations and
baseline identities; this transfer preserves that dependency contract and does
not modify its CI-status implementation. Further GHI dependency updates use
canonical promotion and compatibility tests.

## Role split: `gh_ops.py` vs `gh_identity.py`

| Concern | Owner | Examples |
|--------|--------|----------|
| Cross-repo GitHub REST reads, PR/CI observation, explicit writes | Parent `gh_ops.py` | `pr-observe`, `checks-wait`, `file-put --write`, `commit-files --write`, `pr-merge --write`, `pr-retarget --write` |
| Local git writes on an enrolled checkout (explicit paths only) | Parent `gh_ops.py` | `git-commit --message-file msg.txt path/to/file --write` (optional `--push` with `--write`) |
| Subtree split push from a monorepo to a NEW child branch | Parent `gh_ops.py` | `subtree-push --prefix DIR --remote URL --branch sync/x` (plan), add `--write` to push |
| GitHub Pages delivery evidence (deploy API + HTTP/bytes/text) | Parent `publish_check.py` | `publish_check.py --site … --repo OWNER/OWNER.github.io` |
| Search, existence checks, SHA comparison, pure check classification | `gh_identity.py` | Consumer-vendored read-only inventory and checks helpers |

Consumers should vendor `gh_ops.py` and the parent `LICENSE` directly from this
repository. After `gh_ops.py` changes land on the parent default branch, each
consumer lock that pins `gh_ops.py` must be refreshed to the new commit/blob
(for example `flutter_navigation_basic`).

### Catalog baseline vs consumer locks

`vendor-catalog.json` recommends the accepted parent main commit
`1eda992aa6fa31a902e2c06428e53ab17e8fe3c1`, matching Flutter #197's
reviewed gh_identity request/search delegation contract. The new git-commit
implementation is introduced by #96 but is not yet an adopted consumer pin.
After #96 merges, consumers may explicitly verify and materialize its main
merge commit together with the parent LICENSE. Do not pin its PR intermediate
commits. A consumer repin is separate from this parent registry change.

`gh_identity.py` remains read-oriented in child repositories: it does not replace
`gh_ops.py` for REST writes or local `git add`/`commit`/`push` workflows.

Other parent files that still perform a GitHub or git write are listed in
[the write-overlap audit](gh-ops-write-overlap.md). None of those parent files
was removed: each one still has a caller, and the ones enrolled by a child stay
in place.

## Merge with verification (`pr-merge`)

`pr-merge` merges only when the PR is open, `clean`, its head matches `--sha`, and
the head's check runs are all completed and passing. `--ignore REGEX` drops check
names before judging (default `(?i)copilot`, so a failing Copilot review bot does not
block; `--ignore ''` keeps every check). Ignored checks never count toward
`--min-checks` and are listed under `checks.ignored`.

## Network Git limits (`fetch-guard`)

| Operation | One communication | Attempts plus backoff |
| --- | --- | --- |
| `git-commit --push`, subtree's verified push | 60 seconds | 300 seconds total, at most five attempts |
| `sync-main` fetch / push | 300 seconds each | No retry; fetch and push are separate communications |
| Subtree fetch / clone / ls-remote | Existing subtree `timeout` argument (600 seconds by default) | Separate bounded calls; this is not an overall subtree deadline |

The push helper uses a monotonic deadline. Each attempt is capped at the smaller
of its per-attempt timeout and the remaining total budget; retry waits consume
the same budget. No attempt starts after that deadline. `timeout` and
`total_timeout` are separate Python helper arguments, both positive and finite.
Timeouts and transient connection errors may retry; authentication failures,
host-key failures and rejected pushes stop immediately with the original error.
No force-push or refspec policy is changed.

Network Git receives closed stdin, `GIT_TERMINAL_PROMPT=0`,
`GCM_INTERACTIVE=never`, empty Git/SSH AskPass settings, and
`SSH_ASKPASS_REQUIRE=never`. Guards override inherited/caller prompt settings;
credentials and unrelated environment settings remain available. The default
OpenSSH command uses `BatchMode=yes` only when neither environment variables nor
Git configuration select a custom transport. The priority of `GIT_SSH_COMMAND`,
effective `core.sshCommand` (in the target cwd/environment), and `GIT_SSH` is
preserved, including identity files and wrappers. The configuration lookup shares
the communication budget. Custom transports/helpers must
implement their own noninteractive behavior. Their waiting is still bounded by
the process deadline and they cannot use this process's controlling terminal
on POSIX (a new session is used).

On timeout the real runner kills the POSIX process group or uses Windows
`taskkill /T /F` before killing Git. Output is captured in regular temporary
files rather than pipes, so a transport retaining an output handle cannot make
pipe-draining or Windows reader-thread cleanup wait indefinitely. Cleanup may
add up to one second on POSIX or two on Windows, plus OS scheduling and process
creation overhead. A push timeout can occur after the remote accepted a write;
callers must inspect the remote state before trying a different write. Subtree
push retains its empty-lease/new-branch and post-write verification guards.

Local `add`, `commit`, `status`, `diff`, `rev-parse` and `merge` gain no new
timeout or environment restrictions. Existing subtree local-operation limits
are retained. A whole multi-operation workflow should use its own job timeout;
the 300-second retry budget is not an overall workflow deadline.

Regression tests use a local HTTP/HTTPS server returning a real 401 challenge
and an AskPass positive control, a successful local Git HTTP remote, connection
refusal, an SSH transport with a hanging child, and a deterministic clock for
retry/deadline cases. They require no external network or real credentials.

## PR base retarget (`pr-retarget`)

Stacked pull requests often need their base branch moved (for example from a
feature branch to `main` after the parent PR merged). Planning is the default;
`--write` PATCHes the base only after preconditions pass.

```text
python -S gh_ops.py pr-retarget OWNER/REPO 71 --base main
python -S gh_ops.py pr-retarget OWNER/REPO 71 --base main --write
```

- Compare uses `NEW_BASE...OLD_BASE` (`ahead_by`): commits on the old base that
  are not reachable from the new base would join the PR diff, so `--write` is
  refused unless `--allow-unmerged-commits`. When `ahead_by > 0` but the branch
  tips share the same tree (typical after a squash merge), the unmerged commit
  check is skipped. GitHub may still show parent-branch commits in the PR diff
  (merge-base semantics) even when the trees match; retarget only gates on
  reachability/tree equality, not on diff display.
- A compare response without `ahead_by`, with a null or non-numeric `ahead_by`,
  or with a negative `ahead_by`, is treated like a compare failure (fail-closed).
- Compare failures (for example a deleted old base) refuse `--write` unless
  `--allow-compare-failure`.
- Immediately before PATCH, the PR is re-read: it must still be open with the
  same `base_ref`, head SHA, and old-base tip SHA as at compare time. Optional
  `--expect-head-sha` adds a head pin. Any mismatch refuses without PATCH.
- After PATCH, the PR is read again; `base_ref` must equal `--base` or the
  command exits 1 (`verification_mismatch`).

## Subtree push (`subtree-push`)

```text
python -S gh_ops.py subtree-push --root MONOREPO --prefix DIR --remote URL --branch sync/x          # plan only
python -S gh_ops.py subtree-push --root MONOREPO --prefix DIR --remote URL --branch sync/x --write  # push
```

The decision logic is ported from gh_identity#77 (`cursor/subtree-stale-split-4489`,
head `d43ceb5`). Once #77 is merged and `vendor/gh_identity.py` is repinned to it, the
read-only checks switch to GHI's public functions and only the push stays here.

- The split must have the same tree as `<source>:<prefix>`. `git subtree split` can
  exit 0 with a stale SHA (git 2.47.3); then a filter-branch split in a temporary clone
  is used, and it is checked again.
- The verified SHA is pushed directly as
  `git push --force-with-lease=refs/heads/<branch>: -- URL <split_sha>:refs/heads/<branch>`.
  The empty lease means "the branch must still not exist". A branch someone creates
  after the existence check (even at an ancestor, which a plain push would fast-forward)
  makes the push fail. There is no `git subtree push` (it re-splits), no `--force` and no `+`.
- Error messages name the ref involved (`refs/heads/<branch>`, `remote ref <compare>`).
- Only a NEW branch: main/master, the remote default branch and existing branches are refused.
- The remote compare ref (`--compare-ref`, default: the remote default branch, then
  main/master/trunk) must be resolved and be an ancestor of the split. An explicit
  `--compare-ref` is never replaced by a fallback: a missing ref stops both plan and
  write with `compare_ref_not_found`. If no comparison can be selected, both stop
  with `compare_ref_unavailable` before fetching or splitting. Empty remotes cannot
  be bootstrapped with this command; establish a comparison branch first. Failed
  remote reads or compare-tip fetches also stop without pushing. Paths that exist only on the
  child are refused unless `--child-only-path GLOB` allows them (none by default).
- The compare tip is fetched object-only before splitting (`--no-write-fetch-head
  --no-tags`, no destination ref). After a `--squash` join, `git subtree split` otherwise
  fails with `could not rev-parse split hash`.
- With `--write` the tree is checked once more right before the push. Network errors
  are retried with the `git-commit` loop, and afterwards the remote branch must equal
  `split_sha`. Any unexpected git result refuses the push (exit 2).

Flow for a child repository that was joined with `git subtree add --squash`:

1. `subtree-push ... --branch sync/x --write` from the monorepo.
2. Review and merge `sync/x` in the child repository.
3. In the monorepo: `git subtree pull --prefix=DIR --squash URL main` to re-anchor.
   The next split then descends from the child's main, so the next push is a fast-forward.

## Several files in one commit without a clone (`commit-files`)

```sh
python -S gh_ops.py commit-files OWNER/REPO --branch my-branch --file docs/a.md --file src/x.py=/tmp/x.py --delete old.txt --message "msg"
python -S gh_ops.py commit-files OWNER/REPO --branch my-branch --file docs/a.md --message "msg" --expect-head "$(git rev-parse origin/my-branch)" --write
```

The same blobs/tree/commit/ref sequence as a connector `create_tree` call, but planned
first: each path is listed as `new`, `changed`, `unchanged`, `delete` or `absent` with
its size and Git blob SHA, and a dry run issues no write. Like `file-put`, the default
branch is refused unless `--allow-default-branch`. `--write` uploads only changed blobs,
creates one commit on the current head and moves the branch fast-forward only
(`force: false`); `--expect-head` takes the full 40-hex SHA and refuses unless the branch tip is exactly that commit (a short prefix is rejected, since a moved branch could still match it). Verification re-reads
the branch and the new tree and compares every blob SHA with the local bytes. A
refused ref update leaves an unreferenced commit, which is harmless; re-plan and retry.
At most 100 paths and 10 MiB per file. `file-put` remains the one-file contents-API path.

## Coordinated updates and PR consolidation (`fleet-apply`)

`fleet-apply` takes one JSON manifest for several repositories. Repeated entries
for the same repository are grouped before network access: their files share one
commit and one target PR. Conflicting contents for the same path, conflicting
metadata, write/delete overlaps, missing local inputs and unknown entry fields
are rejected before any remote request. Up to 100 repositories, 100 paths and
100 explicit source PRs per repository are accepted; each file is at most 10 MiB.

```json
{
  "schema": "gh-ops-fleet/1",
  "repositories": [
    {
      "repository": "OWNER/project-a",
      "target_pr": 149,
      "sources": [150, 151],
      "title": "Combine adapters, dependency override and documentation",
      "append_body": "Validation: record the tests actually run here.",
      "files": {
        "README.md": {"from": "prepared/project-a/README.md"},
        "docs/scope.txt": {"text": "Multiple changes, one PR.\n"}
      },
      "deletes": ["docs/obsolete.txt"]
    },
    {
      "repository": "OWNER/project-b",
      "branch": "work/coordinated-update",
      "title": "Apply coordinated update",
      "files": {"tool.py": {"from": "prepared/project-b/tool.py"}}
    }
  ]
}
```

```sh
# Inspect every repository before making changes.
python -S gh_ops.py fleet-apply --manifest fleet.json
# Run independent repositories in parallel; dependent steps within each stay ordered.
python -S gh_ops.py --json fleet-apply --manifest fleet.json --jobs 4 --write > receipt.json
```

`from` paths are relative to the manifest's directory. File inputs are complete
prepared file contents, grouped into the existing `commit-files` Git Data API
operation; they are not unified-diff strings. `message` optionally sets that
commit's message, and `base` defaults to `main`. `append_body` keeps existing PR
text. Files do not need a local checkout of their destination repository.

The workflow is:

1. Plan every repository using read-only requests. Reuse the explicitly selected
   `target_pr`, otherwise the PR for the requested branch, otherwise the sole
   open PR against the selected base. With several candidates, require an
   explicit target; unrelated PRs are never silently designated as sources.
2. Create a work branch only when needed. Refuse the default/base branch and
   refuse a branch already attached to another open PR. Merge each explicitly
   listed source's captured **full head SHA** into the work branch, verifying
   ancestry. A conflict stops that repository before any source PR is closed.
3. Apply all grouped files in one verified commit. Reuse the target PR, or
   re-query the branch immediately before creating one. Update title/body once
   and record each source number/SHA in a consolidated-scope section.
4. Re-read all sources and prove their captured heads are ancestors of the final
   target head. For each still-open source, post and read back its consolidation
   comment, re-check both heads, then close and read back that PR. Sources already
   closed remain closed. The target remains open for review and CI.

Exact repeats skip unchanged file commits, source merges, body blocks, existing
comments and already-closed PRs. Re-running after a lost PR-create response first
looks up the existing branch's PR. A failure in one repository does not stop
independent repositories. Exit 1 means at least one repository is blocked or needs
inspection; `--json` includes completed steps, closed PRs, the failed stage and
remaining sources. The default output gives one compact status per repository.

This is a resumable sequence, not a transaction across repositories. Earlier
verified work is retained after failure. Inspect uncertain receipts before
rerunning: the command never blindly retries a mutation. Parallel invocations on
the same repository are unsupported; GitHub PR creation/PATCH and branch merges
have no atomic compare-and-swap. Immediate re-reads detect observed changes but
cannot exclude changes between the last check and the write. Source ancestry
requires captured commits, so squash/cherry-pick equivalence without those commits
is not accepted as closure evidence. Fork PR heads are currently refused.
Dry runs list requested work; they do not predict every merge conflict. No CI is
run by this command, and ancestry/receipt success does not mean CI success or a
merge to the default branch.

## Edit title and body together (`pr-edit`)

```sh
python -S gh_ops.py pr-edit OWNER/REPO 149 --title "Consolidated change" --append-body-file scope.md
python -S gh_ops.py pr-edit OWNER/REPO 149 --title "Consolidated change" --append-body-file scope.md --write
python -S gh_ops.py pr-edit OWNER/REPO 150 --state closed --write
```

`--body-file` replaces the body exactly, including UTF-8/CRLF/trailing newlines;
`--append-body-file` instead preserves existing text and appends the exact block
once, with a blank-line separator. A block counts as already appended only when
it matches the whole body or a section bounded by body edges or blank lines
(LF, CRLF or CR); `foo` inside `foobar` does not count. Internal block text and
line endings must match exactly. The two flags are mutually exclusive. A repeat
that is already satisfied does no PATCH. Appends re-read the snapshot immediately
before PATCH and stop on observed changes; this is not atomic compare-and-swap.
Mutation uncertainty, successful mutation with failed readback, and exact verified
readback have separate receipts. Existing `pr-body-set`, `pr-body-replace` and
`issue-comment --file` remain available.

## Push an already committed branch (`git-push`)

```sh
python -S gh_ops.py git-push --root path/to/checkout
python -S gh_ops.py git-push --root path/to/checkout --write
```

This pushes the captured HEAD SHA to the current branch on `origin`, without
creating another commit. `--remote NAME` selects a configured named remote;
`--branch NAME` sets the destination and is required for detached HEAD. The
operation has one total `--timeout` budget (default 60 seconds), makes no force,
tag or submodule push, and does not retry. Multiple push URLs are refused. The
result verifies the destination tip against the **push URL**, which may differ
from the fetch URL, and never emits credentials, remote URLs or raw transport
output. A failed/uncertain push or failed readback exits nonzero. Use shell `&&`
to proceed to metadata updates only after a verified push; do not pipe away its
exit status with `tail`.
