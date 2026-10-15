# Shared repository standards

This directory is the canonical source for repository-level agent guidance shared across `myon-bioinformatics` projects.

- `AGENTS.md`: common repository guidance for coding agents.
- `CLAUDE.md`: common Claude-specific behavior and review-only rules.

Repository-local copies may add project-specific requirements, but shared rules should be changed here first to avoid drift. If a local rule intentionally conflicts with a shared rule, document the reason next to the local override so the deviation is explicit and reviewable.

Reusable GitHub Actions live in `.github/workflows/`:

- `reusable-actionlint.yml`: workflow syntax validation callable from other repositories. On `pull_request` and `push`, it detects only changed workflow files by default, avoiding failures caused solely by unrelated legacy workflows. `paths` may be supplied explicitly for other events.
- `reusable-pages-static.yml`: static GitHub Pages deployment callable from other repositories. `artifact_path` is required and deployment is guarded so it only runs for a push to the caller repository's default branch.
- `reusable-parent-pin-check.yml`: scans the caller checkout for commit pins to `myon-bioinformatics/myon-bioinformatics` (reusable workflow `uses:`, checkout `ref:`, docs, test constants, and JSON `repository`/`commit` pairs). Fails when more than one parent SHA is present; stale pins behind parent `main` warn by default (`fail-on-stale: true` to fail). Fleet observation files such as `child-heads.json` are skipped unless `skip-default-excludes` is set.

## Calling reusable workflows

Pin cross-repository reusable workflows to a release tag or, for maximum immutability, a commit SHA. Do not use `@main` in long-lived consumers because changes to the shared repository would otherwise propagate to every consumer without an explicit upgrade.

Example after a stable `v1` tag is published:

```yaml
jobs:
  lint-workflows:
    uses: myon-bioinformatics/myon-bioinformatics/.github/workflows/reusable-actionlint.yml@v1

  deploy-pages:
    uses: myon-bioinformatics/myon-bioinformatics/.github/workflows/reusable-pages-static.yml@v1
    with:
      artifact_path: "dist"

  parent-pins:
    uses: myon-bioinformatics/myon-bioinformatics/.github/workflows/reusable-parent-pin-check.yml@FULL_COMMIT_SHA
    # Default: stale pins behind parent main emit warnings only. Set fail-on-stale: true to fail CI.
```

For `reusable-pages-static.yml`, always pass the actual built/static output directory; do not expose the repository root. Callers must not combine these reusable workflows with `pull_request_target` plus checkout/execution of untrusted fork code.

Canonical public data is maintained separately in `myon-bioinformatics/myon-bioinformatics.github.io`:

- `profile.json`
- `api/repos.json`
- `projects.json`
- `services.json`

This split keeps data ownership and operational templates independent while still providing one authoritative source per concern.
