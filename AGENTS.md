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

All fleet governance lives in **this parent repository only** (`myon-bioinformatics/myon-bioinformatics`): `vendor_sync.py`, `child-heads.json`, parent CI workflows, the distributable child workflow template (`.github/workflows/reusable-vendor-child-ci.yml`), and `templates/parent-pr.ref.json`. Do not split tooling across other repositories.

- **Parent CI** (`run-parent-child-ci`): copies the parent `vendor.lock.json` (and `vendor/`) into a temporary workspace for an offline `check`, compares each locked child's recorded `main` identity to live `main`, and reports branches whose tips are not merged into the child default branch.
- **Identity matching** uses a required 3-tuple: **SHA** (primary), **pull number** (secondary, for parent PR references), and **GitHub repository id** (required; mismatch fails). Other metadata (`created_at`, etc.) may be recorded under `optional` for support but must never fail CI on its own.
- **Auto-fix**: when child `main` SHA drifts, parent CI may open or update a pull request refreshing `child-heads.json` (`--autofix`). It never pushes to `main` and never auto-merges.
- **Children** (future migration): stop maintaining `vendor.lock.json`; copy the parent reusable workflow and supply only `parent-pr.ref.json` (`pull_number` + `repository_id`). Resolve pins with `python -S vendor_sync.py resolve-parent-pr --ref-file templates/parent-pr.ref.json` (merged PR → merge commit SHA, open PR → head SHA).
- Vendor lock `commit` values must already be merged into each source repository default branch (the merge commit), never an intermediate pull-request commit.

## Validation and delivery

- Run the smallest relevant checks already supported by the repository.
- Work on a feature branch and open a pull request; do not merge without explicit approval.
- In each PR, summarize the change, verification performed, and any known limitations.
