# Claude instructions

<!-- Canonical cross-repository template. Repository-local CLAUDE.md may add only project-specific deltas. -->

- Read the relevant issue or pull request, repository documentation, and affected files before making changes.
- Keep changes small and scoped to the request. Do not perform unrelated refactors or dependency upgrades.
- Preserve existing CI, deployment, and GitHub Pages configuration unless the request explicitly requires changing it.
- Run the smallest relevant existing checks and report the result in the pull request.
- Prefer canonical shared data from `myon-bioinformatics/myon-bioinformatics.github.io` instead of copying profile, repository, project, or service endpoint facts into local files.
- Prefer reusable workflows from `myon-bioinformatics/myon-bioinformatics/.github/workflows/` when they cover the repository's need.

## Review-only requests

When asked to audit, observe, or review only, leave findings as comments. Do not edit files, create commits, or open a pull request unless explicitly asked to implement a change.
