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

Stale/mixed pin enforcement is unchanged: schedule and push to main fail,
while pull requests and other branches warn. Coverage warnings do not suppress
pin findings. A green warning-only PR job does not establish fresh pins or
independent review approval.
