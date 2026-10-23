# GitHub write overlap audit

`gh_ops.py` is the parent tool for explicit GitHub and local git writes.
This audit looked for other parent scripts, modules, and workflows that still
perform the same kind of write, then checked callers in this repository,
`vendor-catalog.json`, `vendor-consumers.json`, `vendor.lock.json`, and public
code search under the `myon-bioinformatics` account.

No parent file was deleted. Every parent write path still has a live caller,
and deleting it would drop unique behavior or break a locked consumer.
Child repositories were not edited.

`tests/test_gh_ops_write_surface.py` fails if a new parent `.py`, `.yml`,
`.yaml`, `.mjs`, `.cjs`, or `.js` file outside `tests/` and `docs/` contains
a GitHub write method, `git commit`, or `git push` without being added to the
reviewed set below.

## Candidates

| Path | Overlap with `gh_ops.py` | Callers found | Action |
| --- | --- | --- | --- |
| `gh_workflow.py` | Enables a workflow (`PUT`) and dispatches it (`POST`). `gh_ops.py workflow-dispatch` only posts a dispatch after the workflow is already active. | Ironmate `vendor/gh_workflow.py` (present on the default branch). `vendor-consumers.json` enrolls that destination for Ironmate. `vendor-catalog.json` lists the parent file as a source. Parent callers: `.github/workflows/workflow-wrapper-tests.yml`, `tests/test_gh_workflow.py`, `tests/stdlib_runtime_smoke.py`, `README.md`. | Kept. It checks the workflow YAML for `workflow_dispatch`, can enable a disabled workflow, and records the executed SHA. `docs/gh-ops.md` already says the smaller dispatch does not replace it. Deleting the catalog source would break Ironmate's enrollment. |
| `vendor_sync.py` (`_open_or_update_autofix_pr`, `_dispatch_parent_workflow`) | Creates or force-updates an autofix branch, writes file contents, opens a pull request, and dispatches a parent workflow. | Parent CI `run_parent_child_ci`. Tests under `tests/test_vendor_sync.py` and `tests/test_vendor_child_heads.py`. Catalog source `vendor_sync.py`. | Kept inside `vendor_sync.py`. This is not a separate abandoned file. The autofix step force-moves a branch. `gh_ops.py` commit and push paths are fast-forward only, and `vendor_sync.py` is vendored as one file, so it cannot import `gh_ops.py` without breaking consumers that do not enroll both. |
| `.github/workflows/sync-readme.yml` | `git commit` and `git push` of `README.md`, which `git-commit --push` and `git-push` also do. | The workflow itself: schedule, manual dispatch, and pull requests that touch the README or `scripts/sync-readme.mjs`. The Node script only rewrites the local file. | Kept. The job fetches canonical profile JSON and regenerates README blocks. `gh_ops.py` does not do that fetch. |
| `.github/workflows/update-readme-stats.yml` | `git commit` and `git push` of stats card SVGs. | The workflow itself: schedule, manual dispatch, and pushes to `main` that touch the README or this workflow. | Kept. Card generation is a separate action. Only the final commit overlaps. |
| `vendor/gh_identity.py` | Generic `gh api --method` transport, plus local git writes such as work-branch checkout, fleet pull-request creation, and subtree sync when `--write` is passed. | `vendor.lock.json`. Imported by `gh_ops.py`. `vendor-consumers.json` enrolls it for many locked consumers, including mcp-toolcall-lab, Ironmate, flutter_navigation_basic, and browser-test-kit. | Listed, not edited. The bytes are the locked enrollment. Changing them fails `vendor_sync.py check`. The upstream repository owns the file. This parent does not add writes to it. |
| `browser-test-kit` `scripts/git_commit.py` | Local `git add` and `git commit`, then optional push. Push retry calls the adjacent locked `gh_ops.py`. The add and commit steps are still local, in parallel with `gh_ops.py git-commit`. | `browser-test-kit` `tests/python/test_git_commit.py`. No copy in this parent. `gh_ops.py` records that `git-commit` was migrated from that script. | Listed, not deleted. The file is not in this repository, and that repository is out of scope. Parent behavior already lives in `gh_ops.py` (`tests/test_git_commit.py`). |
| `browser-test-kit` `scripts/app_digest_comment.py` | Posts one issue comment through `gh_ops.issue_comment_create` with `write=True`, after a digest-marker and size check. | `browser-test-kit` `.github/workflows/platform-apps.yml`, `tests/python/test_app_digest_comment.py`, and `tests/python/test_platform_adapter.py`. No copy in this parent. | Listed, not deleted. The marker and size cap are specific to that child. Removing the file there would break the platform-apps workflow. |
| Enrolled copies of `gh_ops.py` (`browser-test-kit` `scripts/gh_ops.py`, `flutter_navigation_basic` `python/vendor/gh_ops.py`, and the other `vendor-consumers.json` destinations) | They are the canonical tool, not a second implementation. | `vendor-consumers.json` entries whose source is `myon-bioinformatics/myon-bioinformatics` and `gh_ops.py`. | Not deleted. They are locked consumer pins of this file. |

## Checked and not a write overlap

`git_inspector.py` only re-exports read-only git observations from the locked
identity module. Flutter and Ironmate catalog enrollment for it is already
`skipped` with reason `retired_after_gh_identity_migration`. It stays as the
compatibility shim described in `docs/git-inspector.md`.

`scripts/apply_repo_patches.py` applies local mailboxes with `git am` and
refuses fetch, push, and pull-request creation.

`publish_check.py` and `ci_status.py` only read GitHub.
