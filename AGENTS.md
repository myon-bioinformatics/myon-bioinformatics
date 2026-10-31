# Repository guidance

## Scope

- Read the relevant documentation, source, and existing workflows before changing anything.
- Keep changes scoped to the request. Do not refactor unrelated code or upgrade dependencies without an explicit request.
- Preserve existing CI, deployment, and GitHub Pages configuration unless the task specifically requires a change.

## Workflow changes

- When modifying `.github/workflows/`, ensure actionlint passes.
- Quote YAML scalar values that contain `: ` or special characters.
- Do not broaden workflow permissions or alter deployment settings without a clear need.

## Parent-owned vendor CI (self-contained in this repository)

All fleet governance lives in **this parent repository only** (`myon-bioinformatics/myon-bioinformatics`): `vendor_sync.py`, `child-heads.json`, parent CI workflows, the distributable child workflow template (`templates/vendor-child-ci.workflow.yml`), and `templates/parent-pr.ref.json`. Do not split tooling across other repositories.

- **Monitored repositories**: only **public** (`visibility == public` and `private == false`), **non-archived** repositories under the `myon-bioinformatics` account (via GitHub API). Private and internal repositories are never monitored, even when an API token could read them. Their names, SHAs, and file contents must not appear in CI logs, `child-heads.json`, pull request bodies, or autofix pull requests. Stale private entries in `child-heads.json` are dropped silently (count only, no names).
- **Parent CI** (`run-parent-child-ci`): copies the parent `vendor.lock.json` (and `vendor/`) into a temporary workspace for an offline `check`, verifies monitored children copied `templates/vendor-child-ci.workflow.yml` verbatim to `.github/workflows/vendor-child-ci.yml` when present, independently verifies registered adopted source Git blobs and child files at recorded SHAs (including the **parent’s own** `vendor.lock.json` source pins via the same merged-into-default check), and reports branches whose tips are not merged into monitored child default branches (**report only**; does not fail CI).
- **Pending enrollment** (`--base-registry`, pull requests only): entries a PR only adds to an existing locked child in `vendor-consumers.json` are reported, not validated, at PR time; they are validated by the child PR that adopts them, and push/schedule runs stay strict (red until the child lock includes them).
- **Identity matching** uses SHA (primary) and GitHub repository id (required). Pull number is required for `parent-pr.ref.json` resolution and optional on `child-heads.json` entries. Other metadata (`created_at`, etc.) may be recorded under `optional` but must never fail CI on its own.
- **Auto-fix**: on schedule/`workflow_dispatch`, a write-capable job may open or update a pull request refreshing `child-heads.json` (`--autofix`). It never pushes to `main` and never auto-merges. A changed lock, workflow, parent reference, repository identity, or new enrollment blocks automatic refresh and requires a deliberate parent baseline review. Pull-request validation uses a read-only job without `--autofix`. An open autofix pull request keeps the same branch name but its head commit moves on each autofix update. The job explicitly dispatches checks on that branch; dispatch failures fail the job.
- **Children / Issue #56 compatibility**: retain the adopted `vendor-lock/1` and `ref=commit SHA` contract. This PR does **not** authorize deleting child locks, retargeting existing reusable workflow pins, or promoting catalog recommendations. `vendor-catalog.json` remains advisory. Parent `vendor-consumers.json` describes topology; `child-heads.json` anchors the immutable child revision whose adopted lock supplies commit/blob/SHA-256 and LICENSE/helper expectations. A lock change requires a reviewed parent baseline update; it is never silently accepted from the child under test.
- **Optional child workflow rollout**: copy `templates/vendor-child-ci.workflow.yml` byte-for-byte to `.github/workflows/vendor-child-ci.yml`, and add `templates/parent-pr.ref.json` with `pull_number`, `repository_id`, and the explicit full `commit` SHA. This PR changes only the parent; no child rollout is performed. Existing child checks (including nested locks) stay in place. The bootstrap verifies the PR number, base repository ID/name and selected SHA before fetching or executing parent code. Open PRs must originate in the parent repository; their heads can move, so a push requires an explicit reviewed SHA update or CI fails. Prefer a merged PR for a stable reference. Runtime checks use the same parent checkout, never a second mutable PR resolution. Reusable workflow refs cannot use `@pr/N` or `refs/pull/*`.

- Vendor lock `commit` values must lie on each source repository default branch **first-parent** history. **Guaranteed:** squash-merge commits and merge commits on `main` are accepted when on that line; no-fast-forward merge **intermediate** commits (for example github.io #39 / web-ui `77ff511` before merge commit `349b1f5`) are rejected. **Not guaranteed:** GitHub **rebase merge** replays pull-request commits onto `main`, so those intermediate commits can appear on first-parent history and pass this check—pin the squash or merge commit when stricter provenance is required.

Consumers may explicitly register an existing locked verification workflow via
optional `locked_ci` (`workflow`, full-file `sha256`) in `vendor-consumers/1`.
Parent CI verifies its regular Git blob at the recorded child SHA, while the
existing adopted source/file audit remains mandatory. An absent optional child
template is intentional for that consumer only if the approved workflow is
present and unchanged. Missing/modified approved workflows fail the audit and
block autofix; updating the approved digest requires parent review. Other
consumers retain the optional-template behavior. See the Flutter decision in
`docs/parent-child-ci-decision.md` for the acceptance conditions and boundaries.

## Validation and delivery

- Run the smallest relevant checks already supported by the repository.
- Work on a feature branch and open a pull request; do not merge without explicit approval.
- In each PR, summarize the change, verification performed, and any known limitations.

- Enforcement boundary: parent CI audits recorded revisions; it is not a required check on every child PR. Missing/uninstalled child workflows remain explicitly unenforced. Do not describe green parent CI as preventing child merges or as independent reviewer approval. See `docs/parent-child-ci-decision.md`.

- Child verification uses parent registry topology for adopted enrollment. Catalog `skipped` controls future recommendations and never invalidates an existing registered copy; source provenance and byte checks remain required.
