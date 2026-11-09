# Shared repository standards

This directory is the canonical source for repository-level agent guidance shared across `myon-bioinformatics` projects.

- `AGENTS.md`: common repository guidance for coding agents.
- `CLAUDE.md`: common Claude-specific behavior and review-only rules.

Repository-local copies may add project-specific requirements, but shared rules should be changed here first to avoid drift. If a local rule intentionally conflicts with a shared rule, document the reason next to the local override so the deviation is explicit and reviewable.

Reusable GitHub Actions live in `.github/workflows/`:

- `reusable-actionlint.yml`: workflow syntax validation callable from other repositories. On `pull_request` and `push`, it detects only changed workflow files by default, avoiding failures caused solely by unrelated legacy workflows. `paths` may be supplied explicitly for other events.
- `reusable-pages-static.yml`: static GitHub Pages deployment callable from other repositories. `artifact_path` is required; the optional `build` input runs a shell command after checkout (with `GH_TOKEN`) to generate it. Deployment is guarded to the caller repository's default branch: a push, or a scheduled / manual run when `build` is set.
- `reusable-parent-pin-check.yml`: scans the caller checkout (or all public children when `fleet: true`) for commit pins to `myon-bioinformatics/myon-bioinformatics`. Audited sources are workflow `uses`/`checkout` refs, inline docs, tests, and `vendor.lock.json` (and similar JSON locks)—not `parent-pr.ref.json`, which children may omit or leave without a parent `commit` now that the checker loads the parent tool from public `main`. **Enforcement policy (default):** `schedule` and push to `main` fail CI on stale or inconsistent parent pins; `pull_request` and push to other branches emit warnings only. Passes `github.event_name` and `github.ref` into the checker automatically. Fleet observation files such as `child-heads.json` are skipped unless `skip-default-excludes` is set.

## Calling reusable workflows

Pin cross-repository reusable workflows to a full 40-character commit SHA (policy since 2026-10-31; tags are not used). Do not use `@main` in long-lived consumers because changes to the shared repository would otherwise propagate to every consumer without an explicit upgrade.

Example (replace `FULL_COMMIT_SHA` with the parent commit you want to consume):

```yaml
jobs:
  lint-workflows:
    uses: myon-bioinformatics/myon-bioinformatics/.github/workflows/reusable-actionlint.yml@FULL_COMMIT_SHA

  deploy-pages:
    uses: myon-bioinformatics/myon-bioinformatics/.github/workflows/reusable-pages-static.yml@FULL_COMMIT_SHA
    with:
      artifact_path: "dist"

  parent-pins:
    uses: myon-bioinformatics/myon-bioinformatics/.github/workflows/reusable-parent-pin-check.yml@FULL_COMMIT_SHA
    # Default policy: enforce on schedule + push to main; warn only on pull_request and non-main pushes.
```

For `reusable-pages-static.yml`, always pass the actual built/static output directory; do not expose the repository root. Callers must not combine these reusable workflows with `pull_request_target` plus checkout/execution of untrusted fork code.

## Workflow file and display names across repositories

Children name a workflow by its role, not by its tooling, so the same job
is found under the same file name and `name:` in every repository
(unified fleet-wide on 2026-11-07):

| Role | File | `name:` |
| --- | --- | --- |
| Main test lane (the one `changes` / `failure-identity` hang off) | `ci.yml` | `CI` |
| Workflow syntax validation (standalone) | `actionlint.yml` | `Validate GitHub Actions` |
| Stdlib-only runtime matrix (`python -S`) | `runtime-compatibility.yml` | `Stdlib runtime compatibility` |
| GitHub Pages build and deploy | `pages.yml` | `Pages` |
| `@claude` mention handler | `claude.yml` | `Claude` |
| Published-platform matrix | `platform-matrix.yml` | `Platform matrix` |
| Shared Python launcher tests | `py-launch-tests.yml` | `py_launch tests` |
| Open WebUI Docker smoke | `openwebui-docker-smoke.yml` | `OpenWebUI Docker smoke` |

Job ids inside the main lane follow the same rule (unified 2026-11-08):

| Role | Job id | `name:` |
| --- | --- | --- |
| Path filter (reusable-change-detection) | `changes` | (default) |
| Resolve the dependency SHAs for later jobs (latest main) | `resolve-vendor` | (default) |
| Run the checked-in vendor baseline (`vendor.lock.json`) | `vendor-locked` | `Vendor locked baseline` |
| Verify the vendor lock only | `vendor-lock` | `Vendor lock` |
| Main test matrix | `test` | `pytest (...)` / `unittest (...)` |
| JUnit collector (reusable-junit-identity) | `failure-identity` | (default) |
| Dispatch the vendor refresh | `vendor-dispatch` | `Vendor dispatch` |

Markdown files under `docs/` are lowercase kebab-case (`docs/vendor-automation.md`);
`README.md`, `AGENTS.md`, `CLAUDE.md` and other repository-level files keep
their conventional upper-case names.

Rules of thumb: file names are lowercase with hyphens (no underscores);
`name:` is a short title-case label; `workflow_run` triggers reference the
`name:` value, so rename both together. Repository-specific lanes keep their
own descriptive names. The parent itself is a hub with several test lanes
(`python-tests.yml`, `node-launch-tests.yml`, ...) and has no single `ci.yml`.

Canonical public data is maintained separately in `myon-bioinformatics/myon-bioinformatics.github.io`:

- `profile.json`
- `api/repos.json`
- `projects.json`
- `services.json`

This split keeps data ownership and operational templates independent while still providing one authoritative source per concern.
