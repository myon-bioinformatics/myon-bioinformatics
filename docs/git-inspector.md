# Shared Git observations

The standard-library-only `git_inspector.py` exposes named read-only operations;
repository identity remains the canonical metadata producer's responsibility.

## File churn history

`log_numstat(root, since=None, max_count=10000, max_bytes=1000000)` returns
`{"commits": [...], "truncated": false}`. Each commit contains its observed
`commit` hash, committer `date` (`%cs`) and a `files` list. Each file contains
`path`, `orig_path` (null unless renamed), `added` and `deleted` (integers,
or both null for binary changes). Filenames use NUL-delimited Git output;
tabs, newlines and Unicode remain filename data. Empty commits remain visible.

Git's default history, merge and rename-detection behavior is retained. A
rename is reported under its destination with the original name recorded
separately, replacing ambiguous human `old => new` notation. `since` is passed
as one `--since=VALUE` argument; no arbitrary Git argv is exposed. Diff/textconv
helpers are disabled.

The operation observes at most `max_count + 1` commits to detect the count
limit, publishes at most `max_count` complete commits and sets `truncated`
when count or byte limits are reached. It omits a byte-truncated final commit
in full. Consumers must check `truncated` before presenting totals as complete.
Missing Git, invalid Git dates or non-worktrees raise `GitInspectionError`;
they do not become empty successful observations.

This API enables the Wave 2 repo_overview migration without a consumer-local
numstat/NUL parser. Consumers must vendor only after merge and pin the resulting
commit with provenance. It does not change the existing `log()` contract.
