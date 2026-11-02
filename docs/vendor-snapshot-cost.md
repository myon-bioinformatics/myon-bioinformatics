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
