# Batched Git snapshot inspection

The parent snapshot reader already reuses immutable blobs within one audit
(#202). For each remaining snapshot, it now obtains the regular-file mode,
blob ID and size for all requested paths with one `git --literal-pathspecs
ls-tree -rlz --full-tree` call. NUL-delimited exact paths preserve spaces,
non-ASCII names and bracket characters. Each requested file must appear as
100644/100755 blob; missing paths, directories, symlinks and gitlinks fail.
The existing per-file byte limit is checked before `cat-file blob` reads data.
Commit selection, first-parent checks, repository IDs, SHA-256 verification,
credential isolation and retry behavior are unchanged.

This uses Git's local object tree, not the GitHub REST recursive-tree API, so
there is no REST `truncated` response to mistake for a complete tree. It does
not enumerate entire repositories for discovery or change any adopted lock.

## Reproducible comparison, 2026-11-02

Compared `_read_public_git_snapshot` at parent main
`a92ece5` (the unmodified worktree base) with the modified reader. Both read the
same committed four-file fixture (`a`, `b`, `nested/c`, `LICENSE`) from a local
file remote, with one uncached invocation per implementation. Process counts
were collected by wrapping `subprocess.run`; outputs were compared directly.

| Measure | Before | After |
| --- | ---: | ---: |
| Git processes, including init/fetch/rev-parse | 15 | 8 |
| ls-tree calls | 4 | 1 |
| cat-file size queries | 4 | 0 |
| cat-file content reads | 4 | 4 |

Resolved commit, per-file blob IDs and bytes were equal. This establishes
subprocess-count reduction on the local fixture, not hosted-CI latency,
whole-fleet throughput or lower network transfer volume. The earlier #202
fetch-reuse comparison (3→2) measures a separate improvement.

Validation includes real-Git regular/executable files and literal nested,
Unicode, space and bracket names; existing missing/symlink/gitlink/oversize
and ambient-credential rejection tests remain in the relevant suite.


## Promisor verification, 2026-11-03

The local regression server explicitly enables `uploadpack.allowFilter` and is
accessed through a `file://` remote. Immediately before sizing, `rev-list
--objects --missing=print FETCH_HEAD` proves both fixture blobs are absent.
The normal case then checks commit/blob/bytes for both requested files. The
oversized case rejects before any `cat-file blob` content read. This validates
lazy blob sizing rather than a local server that ignores blob:none.

A separate read-only probe used the actual GitHub parent at immutable commit
`d04de02a93d06f87fb5ae97bf86d43496f0ec157`. Before sizing, 374 blob objects were
missing. The reader successfully returned LICENSE (1061 bytes, SHA-256
`15f66204c4a6a1ce0c94f0ed4319ff9400f872e85fd32c95f21695c2f231af59`)
and vendor_sync.py (129453 bytes, SHA-256
`42a723ffafb2fa25fca66f32eccb8a0aa42a53d40db3818961493ed9e919514e`).
Both match the clean checkout bytes at that revision. The two controlled
file-remote cases are offline CI regressions; the public GitHub probe is a
one-off observation, not an added network test or a latency benchmark.
