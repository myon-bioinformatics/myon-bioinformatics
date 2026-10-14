# Repository guidance

## Scope

- Read the relevant documentation, source, and existing workflows before changing anything.
- Keep changes scoped to the request. Do not refactor unrelated code or upgrade dependencies without an explicit request.
- Preserve existing CI, deployment, and GitHub Pages configuration unless the task specifically requires a change.

## Workflow changes

- When modifying `.github/workflows/`, ensure actionlint passes.
- Quote YAML scalar values that contain `: ` or special characters.
- Do not broaden workflow permissions or alter deployment settings without a clear need.

## Vendor pins, child heads, and parent PR refs

- Vendor lock `commit`/`ref` values must already be merged into the source repository default branch (use the merge commit), never an intermediate pull-request commit; re-pin before merging consumer changes.
- The parent tracks each locked child default-branch tip in `child-heads.json`. Scheduled CI fails when live `main` drifts from that record; refresh with `python -S vendor_sync.py refresh-child-heads` and commit the update (CI never auto-commits).
- Child repositories should reference parent reusable workflows by pull request number (`...@pr/N` or checkout `ref: pr/N`); children's CI resolves the pin with `python -S vendor_sync.py resolve-parent-pr --pr N` (merged pull requests use the merge commit, open pull requests use the head commit). Do not hand-pin parent commit SHAs in consumer workflows.

## Validation and delivery

- Run the smallest relevant checks already supported by the repository.
- Work on a feature branch and open a pull request; do not merge without explicit approval.
- In each PR, summarize the change, verification performed, and any known limitations.
