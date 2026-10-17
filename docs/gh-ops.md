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
| Cross-repo GitHub REST reads, PR/CI observation, explicit writes | Parent `gh_ops.py` | `pr-observe`, `checks-wait`, `file-put --write`, `pr-merge --write` |
| Local git writes on an enrolled checkout (explicit paths only) | Parent `gh_ops.py` | `git-commit --message-file msg.txt path/to/file --write` (optional `--push` with `--write`) |
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
