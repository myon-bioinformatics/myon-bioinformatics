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
- **Parent CI** (`run-parent-child-ci`): copies the parent `vendor.lock.json` (and `vendor/`) into a temporary workspace for an offline `check`, verifies monitored children copied `templates/vendor-child-ci.workflow.yml` verbatim to `.github/workflows/vendor-child-ci.yml` when present, and reports branches whose tips are not merged into monitored child default branches (**report only**; does not fail CI).
- **Identity matching** uses SHA (primary) and GitHub repository id (required). Pull number is required for `parent-pr.ref.json` resolution and optional on `child-heads.json` entries. Other metadata (`created_at`, etc.) may be recorded under `optional` but must never fail CI on its own.
- **Auto-fix**: on schedule/`workflow_dispatch`, a write-capable job may open or update a pull request refreshing `child-heads.json` (`--autofix`). It never pushes to `main` and never auto-merges. Pull-request validation uses a read-only job without `--autofix`. An open autofix pull request keeps the same branch name but its head commit moves on each autofix update. The job explicitly dispatches checks on that branch; dispatch failures fail the job.
- **Children / Issue #56 compatibility**: retain the adopted `vendor-lock/1` and `ref=commit SHA` contract. This PR does **not** authorize deleting child locks, retargeting existing reusable workflow pins, or promoting catalog recommendations. `vendor-catalog.json` remains advisory. Parent `vendor-consumers.json` describes topology; `child-heads.json` anchors the immutable child revision whose adopted lock supplies commit/blob/SHA-256 and LICENSE/helper expectations. A lock change requires a reviewed parent baseline update; it is never silently accepted from the child under test.
- **Optional child workflow rollout**: copy `templates/vendor-child-ci.workflow.yml` byte-for-byte to `.github/workflows/vendor-child-ci.yml`, and add `templates/parent-pr.ref.json` with `pull_number`, `repository_id`, and the explicit full `commit` SHA. This PR changes only the parent; no child rollout is performed. Existing child checks (including nested locks) stay in place. The bootstrap verifies the PR number, base repository ID/name and selected SHA before fetching or executing parent code. Open PRs must originate in the parent repository; their heads can move, so a push requires an explicit reviewed SHA update or CI fails. Prefer a merged PR for a stable reference. Runtime checks use the same parent checkout, never a second mutable PR resolution. Reusable workflow refs cannot use `@pr/N` or `refs/pull/*`.

- Vendor lock `commit` values must already be merged into each source repository default branch (the merge commit), never an intermediate pull-request commit.

## Validation and delivery

- Run the smallest relevant checks already supported by the repository.
- Work on a feature branch and open a pull request; do not merge without explicit approval.
- In each PR, summarize the change, verification performed, and any known limitations.
