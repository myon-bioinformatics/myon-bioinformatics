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

- **Monitored repositories**: every non-archived repo in the `myon-bioinformatics` org (via GitHub API) appears in `child-heads.json`. Parent CI compares recorded `main` identity (SHA + repository id) to live `main`. Optional `pull_number` on a head entry is informational when resolvable; child-head checks never require pull number (only SHA + repository id).
- **Parent CI** (`run-parent-child-ci`): copies the parent `vendor.lock.json` (and `vendor/`) into a temporary workspace for an offline `check`, verifies locked children copied `templates/vendor-child-ci.workflow.yml` verbatim to `.github/workflows/vendor-child-ci.yml`, and reports branches whose tips are not merged into locked child default branches (**report only**; does not fail CI).
- **Identity matching** uses SHA (primary) and GitHub repository id (required). Pull number is required for `parent-pr.ref.json` resolution and optional on `child-heads.json` entries. Other metadata (`created_at`, etc.) may be recorded under `optional` but must never fail CI on its own.
- **Auto-fix**: on schedule/`workflow_dispatch`, a write-capable job may open or update a pull request refreshing `child-heads.json` (`--autofix`) and dispatch `workflow_dispatch` on the autofix branch so checks run. It never pushes to `main` and never auto-merges. Pull-request validation uses a read-only job without `--autofix`.
- **Children**: do not maintain `vendor.lock.json`. Copy `templates/vendor-child-ci.workflow.yml` byte-for-byte to `.github/workflows/vendor-child-ci.yml` and commit only `templates/parent-pr.ref.json` (`pull_number` + `repository_id`). The child workflow resolves the parent pull request via the GitHub API (no unpinned parent `main` code), fetches the parent at that commit, then runs `python -S vendor_sync.py run-child-ci` from that pin. Reusable workflow refs cannot use `@pr/N` or `refs/pull/*`.
- **Private child checkouts**: set repository secret `GH_READ_TOKEN` (read access to parent and vendored sources). When unset, `GITHUB_TOKEN` is used (sufficient for public repos).
- Vendor lock `commit` values must already be merged into each source repository default branch (the merge commit), never an intermediate pull-request commit.

## Validation and delivery

- Run the smallest relevant checks already supported by the repository.
- Work on a feature branch and open a pull request; do not merge without explicit approval.
- In each PR, summarize the change, verification performed, and any known limitations.
