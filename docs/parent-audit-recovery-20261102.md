# Parent audit recovery — 2026-11-02

Baseline: parent main `8ce289a78754cf8ca0ae2ca3889b52281929fdc4`.

## Observations and bounded repair

The scheduled [Parent child CI run](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/41404515715)
reported 16 advanced observation anchors, zero repository identity failures and
zero adopted-file/provenance failures at the recorded revisions. Missing optional
workflow copies (16) are separate, unenforced findings. The autofix POST to the
pulls endpoint returned 403 with remaining quota 4996 and rate_limited=false.
The old log discarded the API message, so its exact policy/permission cause
cannot be recovered from that log. This is not an intentional negative test.

This repair recognizes three exact known API messages and emits only fixed
reason codes: `actions_pr_creation_disabled`, `integration_access_denied`, and
`token_access_denied`. Bodies, repository paths, unknown messages and credentials
are not logged. Body reads are bounded to 8193 bytes; over-limit and malformed
responses remain unclassified. Writes are still never retried and failures
remain failures.

Eight observation anchors are refreshed: Err-o-RoadRunner, ascii_artist,
gh_identity, markdown, the parent itself, nvd_nist_known_vulns,
search_seq_including_spaces, and yourself. The public/non-archived repository IDs
match the recorded IDs. All 48 placed files at the newly selected immutable
revisions were read independently through GitHub and match both lock Git blob
SHA and SHA-256. Their adopted locks are unchanged from the reviewed fixtures.
The new fixture records the revision and canonical JSON lock digest, preserving
all historical lock fixtures. Parent CI must still independently verify sources
on their default-branch first-parent histories at this PR's exact head.

No catalog recommendation, consumer topology, adopted lock, approved workflow
digest, permission or child workflow is changed. The parent's own observation
stops at the pre-PR main revision; merging this PR will naturally advance main
again. This is a finite observation, not a perpetual zero-drift assertion.

## Remaining work

Eight advanced children have changed adopted locks compared with their recorded
anchors and need a deliberate source/lock review before re-observation:
Aoi, Ironmate, browser-test-kit, cli_args, mcp-toolcall-lab,
myon-bioinformatics.github.io, web-ui and xprobe. Their anchors are retained.
Flutter's selected revision is unchanged. Do not waive these checks or overwrite
adopted source hashes merely to make the scheduled check green.

The next failed autofix run can distinguish known 403 causes. For
`actions_pr_creation_disabled`, a repository administrator should inspect
Settings → Actions → General → Workflow permissions → Allow GitHub Actions to
create and approve pull requests, including any organization policy. For an
integration/token access denial, inspect the credential's repository access and
pull-request write permission. A rate-limit finding needs separate handling.
The current workflow already requests contents/pull-requests/actions write in
its autofix job; adding more workflow permission declarations cannot override a
repository/organization restriction. No setting was inspected or changed here.
Do not infer one of these causes from a generic 403, bypass policy with a second
credential, retry an ambiguous write, or report automatic recovery as verified.

Track continuing fleet governance in [issue #56](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/56).
