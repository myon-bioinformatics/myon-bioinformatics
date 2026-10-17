# Parent pin fleet coverage

`python tools/parent_pin_check.py --fleet --child-heads child-heads.json`
always enumerates the account's public, non-archived repositories through the
GitHub API. It excludes the parent itself and repositories owned by another
account. All inventory pages must succeed; invalid metadata, a failed page or
the pagination limit stops the command with exit 2. An authenticated 401/403
permits one anonymous public-list retry, never a registry-only fallback.
Public metadata, tree and compare API reads also use the supplied token to
avoid exhausting the shared anonymous quota, with one anonymous retry on
401/403. Raw files and Atom feeds remain unauthenticated. Token access never
overrides public/non-archived eligibility checks.

`child-heads.json` remains the known HEAD/adopted baseline used by parent vendor
validation. Fleet freshness audits each live default branch, not the recorded
baseline SHA. The checker cross-checks registry membership against the complete
public inventory and includes unregistered children such as `xprobe`. It reports
their names as a warning after a successful audit. This does not automatically
enroll them or change the approved baseline. Unmatched registry entries are
counted without disclosing names or SHAs.

Before fetching child contents, the checker revalidates public/non-archived
eligibility and resolves the actual default branch. Confirmed private, internal
or archived children are excluded; missing repositories retain the existing
404 skip policy. Fetch errors remain distinct from these exclusions. Reports
include successful audit counts and `complete: false` for audit errors or
authentication/rate-limit warnings. Existing per-child 401/403 warning policy
is unchanged; an unavailable fleet inventory is always fatal.

The command's event policy is unchanged: schedule and push to main enforce,
while pull requests and other branches warn. Coverage warnings do not suppress
pin findings. This is distinct from enabling an automatic workflow gate.

## Staged rollout in PR #91

Automatic fleet enforcement is **not enabled** by #91. The prior live audit
found 16 stale/mixed child pins; a green ordinary PR/schedule run is not proof
that those pins were corrected. The existing scheduled vendor-baseline audit
and its permissions remain in place. Follow-up distribution and gate activation
are tracked in parent issue [#56](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/56).
The parent remains the only source of the shared workflow/tool policy; no child,
including gh_identity, receives an exception.

To run the strict, read-only readiness audit, dispatch
`vendor-parent-child-ci.yml` with `strict-pin-readiness=true`. That mode skips
the unrelated validation/autofix jobs and runs:

```sh
python tools/parent_pin_check.py --fleet --child-heads child-heads.json \
  --enforce --require-complete --output reports/parent-pin-readiness.json
```

Findings still exit 1. `--require-complete` additionally rejects incomplete
audits (including authentication warnings); API/configuration failures exit 2.
The JSON report is preserved without changing its finding counts or `ok`/
`complete` fields, including on exit 1. A pre-report fatal error can leave only
the diagnostic log. The workflow appends the log to its summary and always
attempts 14-day artifact retention. It does not turn strict failures into
successful checks.

Before enabling a default main/schedule fleet gate, distribute the reviewed
parent workflow through the same process to every public child, review any
changed adopted workflow baselines in the parent, and require a complete strict
audit with zero findings. The follow-up must also define the update window:
every new parent main commit makes exact old main pins stale, so a one-off pin
sweep alone does not establish a sustainable strict default gate.
