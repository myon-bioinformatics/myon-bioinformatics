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
