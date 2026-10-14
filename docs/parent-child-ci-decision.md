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
