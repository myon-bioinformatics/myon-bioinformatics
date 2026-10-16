# Parent/child vendor CI: decision and enforcement boundary

Decision recorded on 2026-10-14 for parent PR #78. Keep the implementation in
one parent repository and one PR. Do not merge or roll out child changes here.

## Goal and evidence

The operational goal is centrally maintained, automatic vendor distribution on
GitHub, without a separate child PR for every refresh or a bespoke updater in
each child. Mandatory merge gates are a separate, stronger enforcement option;
they are not a prerequisite for this distribution goal.

A consumer must not be able to make vendor verification pass by editing both
its files and the hashes or verification procedure that judge those files.

- [web-ui #29 review](https://github.com/myon-bioinformatics/web-ui/pull/29#pullrequestreview-5361289568)
  identified editable file/provenance hashes as circular evidence.
- [web-ui #50](https://github.com/myon-bioinformatics/web-ui/pull/50) added the
  missing mandatory upstream browser regression. It is not evidence of a
  deliberately weakened vendor check.
- [portfolio #39 correction](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/pull/39#issuecomment-6164021970)
  acknowledges merging before repinning to the final upstream merge commit;
  #43 corrected the provenance even though runtime bytes were unchanged.
- [Parent Issue #56](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/56)
  adopted `vendor-lock/1` and commit SHA pins. Retain these contracts.

## Selected best effort for this PR

Reuse existing locks and identity/bootstrap checks. Do not introduce another
manifest format, remove child locks, or add a write-capable child workflow.

1. Parent Actions reads adopted locks at the immutable revisions in
   `child-heads.json`. Registry topology must match.
2. Treat lock digests as claims: independently read regular Git blobs at each
   source commit, verify source blob/SHA-256, then verify the actual child files.
   No source or child code is executed. Fetch/identity/digest failures fail CI.
   This check also runs when parent baseline revisions are manually updated.
   The parent itself keeps its existing candidate-update ref contract and is
   checked from the current PR checkout against the exact recorded source
   commit/blob/digest; it is not treated as a child with a migrated lock.
3. Automatic head refresh may not change the contract: compare adopted locks
   (including the known nested html-import lock), all workflow definitions and
   the parent reference before opening/updating the refresh PR. Missing locks,
   workflow removal, changed parent references, new repositories and repository
   ID changes require deliberate parent review. No automatic adoption or merge.
4. Continue the child verifier as defense in depth; bootstrap checks PR number,
   repository identity and explicit SHA before executing parent code.

GitHub Actions orchestrates; GitHub API resolves identity and metadata. Reading
Git objects through the existing bare-Git helper avoids the Contents API's
symlink ambiguity. This is reuse of existing verification, not a second
independent distribution framework.

## Recommended distribution shape (design, not deployed by this PR)

Use a scheduled/manual thin caller in each child and a single parent-owned
reusable workflow/engine. Public API reads can use ordinary HTTP; the child's
standard GITHUB_TOKEN can provide contents:write for writing its own repository,
subject to its existing branch rules. A fleet-wide GitHub App is not a baseline
requirement for this design. Never bypass branch rules automatically.

The engine pin and distribution revision have different jobs: keep the reusable
engine at a verified full SHA, resolve the parent main once through the API,
verify repository identity, and read all distribution data at that immutable
SHA. This permits new parent-approved data without editing each child caller.
Resolve/check upstream repository IDs, PR provenance where supplied, and source
main SHAs before deriving file expectations. Do not execute freshly downloaded
code before identity verification or treat an arbitrary child hash as evidence.

Separate observed child heads from approved distribution targets. The current
`child-heads.json` is an observation/adopted-lock anchor, not yet an automatic
latest-source release manifest. Merely refreshing it cannot provide this updater.
Use the existing registry and SHA-pinned lock contract when implementing the
approved distribution targets; do not silently promote advisory catalog rows.

When an approved distribution differs, verify the previous canonical bytes,
replace only registered vendor paths and their lock, and commit the whole batch
with expected-head concurrency checking. Prefer scheduled synchronization over
PR-opening automation: it avoids fork/write-token complications and recursive
PR events. No per-update PR is needed where repository policy permits this
narrow direct update. If there is a local modification, missing source, identity
mismatch, or concurrent branch change, stop instead of silently overwriting or
inventing a child-specific exception. No diff means no commit.

Run the agreed validation in the synchronization workflow before committing;
if post-commit CI is required, dispatch it explicitly and bind evidence to the
new SHA. GITHUB_TOKEN pushes do not trigger ordinary push workflows. See
[GitHub token documentation](https://docs.github.com/en/actions/concepts/security/github_token).

The three-way proposal is optional. Exact canonical vendor copies usually need
only old-baseline/current/new-target comparison, not textual merging. Textual
merging could produce a fourth version that matches neither canonical source.
Start with exact replacement when the current bytes match the previous approved
baseline; surface independent edits as conflicts. This is the smaller default.

## Alternatives and tradeoffs

| Option | Benefit | Cost / limitation | Decision |
| --- | --- | --- | --- |
| Child-local hashes and workflow only | Smallest setup | Child can change both claim and verifier | Insufficient |
| Parent independent audit + guarded baseline refresh | Stops circular evidence and silent contract adoption | Recorded revisions; no mandatory per-child-PR merge gate | Implement now |
| Thin scheduled child caller + parent-owned sync | Automatic updates without per-update child PRs or duplicate logic | Approved target data, narrow writes, concurrency/validation and initial rollout | Recommended distribution design |
| Parent-owned check bound to every child PR SHA and required by rules | Enforce a merge gate independent of child edits | Cross-repository permissions, triggering and rules rollout | Optional stronger enforcement |
| Three-way update and sync commit on an existing child PR | Avoid a separate sync PR for each update | Conflict handling, write credentials, concurrency and CI retriggering | Optional alternative to scheduled delivery |

The user's three-way proposal is an option, not a requirement to add complexity
now. Its three inputs would be the last approved distribution, the current child
contents, and the newly approved distribution. Only a parent-approved result is
eligible for delivery. A textual merge that succeeds does not prove that a
modified vendor file still satisfies the canonical source contract. Independent
child edits must conflict or become an explicitly reviewed upstream change.

If automatic delivery is later adopted, commit the approved bytes to the existing
child PR using an expected-head lease and run checks on the resulting SHA. Never
modify only the CI workspace and report success for a different committed tree.
For forks or missing write permissions, fail with an actionable update request.
Do not assume a push using GITHUB_TOKEN automatically triggers a new CI run.

## What success does not establish

This PR does not install required checks/rules in children, independently audit
all child PR heads, or prevent an authorized actor from modifying parent policy.
An absent child workflow is reported as **not enforced**, not protected. Once a
workflow exists at the recorded baseline, its removal cannot be accepted by the
automatic refresh path. A deliberate parent update still needs review.

The source-byte audit proves content provenance at the claimed immutable commit;
it does not prove that commit is the final merge SHA of a particular upstream
PR. The existing reachability audit also cannot prove that distinction. Exact
upstream PR adoption must retain explicit PR/merge evidence in its review; green
CI must not be described as having automated that policy or resolved an
outstanding review. Additional nested/proprietary manifest formats remain under
existing consumer checks until explicitly registered.

Previous statements that the four Blocking fixes or green CI established full
anti-bypass enforcement are withdrawn. Implementation self-review is not an
independent approval. Report implemented checks, CI results and these remaining
boundaries separately.

## Adopted enrollment vs recommendations

The parent registry is authoritative for the topology of already adopted copies.
Catalog `skipped` excludes a tool from future recommended distribution; it does
not revoke an existing registered lock entry. Child verification still requires
exact registry topology, immutable source provenance, source blob/SHA-256 and
actual child bytes. Unregistered additions remain errors. Ironmate provides no
catalog tools; its existing consumer copies are retained.

## Flutter after removal of the optional child template

Updated on 2026-10-16 after Flutter #193 and parent #88/#89. The proposed
Flutter #189 audit snapshot is `5ade883ced2709b5c6843f46db0a75d8dcae9983`
(unmerged at preparation); its existing
`.github/workflows/non-dart.yml` is approved in `vendor-consumers.json` through
the optional `locked_ci` object (`workflow`, full-file `sha256`). The registry
and `child-heads.json` are updated together for the 14 relocated files and
`python/vendor.lock.json`. Only the `tool/` destination prefix changes; source
repositories, source paths, commit/ref pins, blob IDs and digests remain identical
to Flutter main `42cb142e1dde78e8194597cca159bfe20f067bba`.
No replacement workflow, child updater, parent PR reference, new lock format,
or duplicate job is introduced.

| Acceptance condition | Existing owner / evidence |
| --- | --- |
| Child identity and immutable audit revision | Parent live repository ID vs `child-heads.json`; public non-archived scope |
| Registered files, helpers and LICENSEs | Parent registry topology vs the adopted `vendor-lock/1` at the recorded child SHA |
| Immutable source pins and upstream provenance | Parent requires `ref=commit`, public source, default first-parent commit, regular source blobs and exact blob/SHA-256 |
| Actual placed bytes | Parent independently checks child Git blobs; Non-Dart `python-vendor-locked` also runs offline `check` |
| Reproducible placement | The same Non-Dart job removes placed files, materializes from locked commits, and checks again before metadata regressions |
| Vendor input changes run the lane | Non-Dart push/PR paths cover `python/**` and its own workflow; the classifier selects Python/workflow changes; manual dispatch selects all |
| Verification cannot silently disappear | Parent checks the registered workflow's full bytes from a regular Git blob at the recorded SHA against the parent-owned digest |

The approved workflow checks out the parent tools at
`b000e5967cb7596f6f9f20fe396a686fdc700c16`, with credentials disabled and read-only
permissions. Source trust is supplied by the independent parent audit; local
`check` alone is not equivalent to the old bootstrap. A PR-number bootstrap is
unnecessary for this existing full-SHA tool checkout. Existing artifact staging
and metadata regressions remain in that lane.

For a consumer explicitly registered with `locked_ci`, absence of the optional
`vendor-child-ci.yml` is intentional only when the approved workflow bytes are
present. A missing, modified, unreadable or non-regular approved workflow fails
the parent audit and blocks automatic head refresh. If the old template is
still present, its byte agreement is still required. For other consumers, a
missing optional template remains visible as unenforced; this change does not
claim a fleet-wide rollout.

The digest deliberately covers the whole workflow, including triggers, path
classification, job conditions, commands, tool pins and error handling. This
avoids a partial YAML parser or token-presence check in the stdlib runtime.
Even an unrelated legitimate Non-Dart edit needs an explicit parent digest
review/update; autofix never updates that approval. Reviewers must inspect the
new workflow before approving its digest. Hash equality establishes agreement
with reviewed content, not that arbitrary newly approved content is safe.

Parent CI still audits recorded revisions, not every live child PR. Its green
result does not prove a child workflow actually ran, establish required branch
checks, or prevent child merges. Use a CI run for the selected child SHA as
separate execution evidence. Child-head drift remains separately reported.

### Coordinated integration of Flutter #189 and parent #90

The approved full-file SHA-256 is `dde7e2ed112eeb9cb92c8c6eefe51f70808f1085510c2d579f97c674088e3d6b`. Both Flutter
workflows now select PR base `main` only; the temporary
`feat/image-diff-tool` base and its test expectation have been removed. The
Non-Dart paths/classifier and check -> remove -> materialize -> check sequence
use `python/`; pinned parent tooling, failure propagation, metadata regressions
and artifact preservation are retained.

This parent PR deliberately proposes the exact **unmerged** #189 snapshot for
read-only audit, with `pull_number: 189`. It does not claim that SHA is current
Flutter main or that either PR has been approved. The registry lock path, all
14 destination paths, workflow digest and child-head snapshot form one reviewed
baseline change. Changing only the digest would break the old recorded snapshot;
changing only paths would make its adopted lock unreadable. No alternate digest
or path fallback is accepted.

Integration order (neither merge is automated):

1. Review both final heads and their same-head CI, then merge Flutter #189 first.
2. Read the actual Flutter main merge SHA. If squash/rebase/merge creates another
   SHA, update only Flutter's `child-heads.json` record to that full SHA in parent
   #90, retaining repository ID and PR provenance. Recheck that the workflow and
   relocated lock bytes match these reviewed fixtures.
3. Rerun parent pytest and Parent child CI on that final parent head, then review
   and merge parent #90. Do not merge the parent candidate before the child.

A later child main SHA with the same approved workflow bytes passes the workflow
refresh gate; different/deleted/disabled workflow bytes or a snapshot SHA mismatch
still return `locked_ci_mismatch`. The separate source/topology/file audit
continues to gate baseline refresh. The parent does not automatically approve
a changed digest or infer a relocation from the child under test.

## Product CI for rollout-only changes

Reusable change detection adds `product_changed`, while preserving all existing
outputs. Only `.github/workflows/vendor-child-ci.yml` and
`templates/parent-pr.ref.json` are excluded from product changes, along with
existing docs-only paths. Vendor bytes, locks, dependencies, other workflows and
unknown paths still enable product tests. `force_heavy_paths` wins over these
exclusions. Empty diffs and manual runs enable product tests; diff failures fail
the classification job. Consumers may adopt this output only while retaining
independent Vendor child CI and actionlint. This does not weaken vendor auditing
or change the existing docs-as-test-input override contract.
