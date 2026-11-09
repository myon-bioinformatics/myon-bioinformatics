# Actions dependency lockfile pilot

The read-only `Actions lockfile pilot` workflow checks that the v0.4.1 release
tag is the annotated tag object `a8846ab4e4c1e34a7c40f65f2e78e98066913bf0`
and that this object points to commit
`04b7a1a4d00b339dd3ff903bb4d78d4066b2bac7`, then installs
the official release binary using `gh extension install --pin v0.4.1`.
The binary is release-distributed, not locally built from that source commit.
The generator runs against an isolated copy
of `workflow-wrapper-tests.yml`. It then runs online provenance verification and
offline coverage verification. The candidate workflow and machine-generated
`actions.lock` are retained together in the `actions-lock-pilot` artifact for
14 days. No write token, automatic commit, or automatic adoption is used.

## Review before adoption

- Compare each generated action commit with the current reviewed 40-character
  SHA. Investigate any difference before adoption, including transitive actions.
- Inspect repository and owner IDs and the generated workflow diff.
- Parent `scripts/check_action_uses.py` still requires full SHA references with
  version comments for workflows without an adopted lockfile. The integration
  below permits exact version tags only for individually onboarded workflows
  whose dependencies are validated by both the scanner and the official tool.
- Lockfiles do not lock reusable workflow calls, runner images, input versions
  (such as uv `version: latest`), downloaded programs, or container images.
- Reusable workflow calls retain their existing full-SHA pins. The parent/child
  vendor registry and child enrollment remain separate from action dependency locks.
- Only after review should the generated candidate be adopted with a verification
  check and a successful actual run of the onboarded workflow. Artifact generation
  alone does not demonstrate runner-side lockfile enforcement or Dependabot updates.

The pilot is triggered by relevant pull requests or manually. It is not scheduled.

References:
- https://docs.github.com/en/actions/concepts/security/workflow-dependency-lockfiles
- https://github.com/github/gh-actions-lock/tree/04b7a1a4d00b339dd3ff903bb4d78d4066b2bac7

## Adoption and audit integration

The generated lockfile from successful pilot run 41441021443 is adopted for
`workflow-wrapper-tests.yml` only. Its three action commits match the previously
reviewed full SHAs. The workflow uses the exact version tags recorded by that
machine-generated lockfile. Lock-only changes also trigger its test matrix.

`scripts/check_action_uses.py` accepts tagged actions only when that exact
reference is recorded for that exact workflow in `actions.lock`. All other
workflows and child templates retain the full-SHA and release-comment policy.
Reusable workflow references cannot use the action-lock exemption.

The stdlib scanner accepts only the official v0.0.3 YAML formatting observed in
the generated artifact. Unsupported formatting, duplicate keys, missing records,
invalid commit/ID values and unknown versions fail closed. Regenerate with the
reviewed official tool; do not hand-edit the lockfile. A future generator/schema
change may require updating this deliberately restricted reader.

Online audit checks each locked dependency's release commit, repository ID and
owner ID, including recorded transitive dependencies. A mismatch or API error
cannot be waived with `--warn`. The official tool also verifies every onboarded
workflow in CI, checking dependency coverage and upstream provenance; this is
required in addition to the lightweight stdlib audit. The offline audit alone
does not inspect upstream composite-action contents or prove dependency closure.

The first actual onboarded CI run and artifact preservation must succeed before
merging. Dependabot's future lock regeneration remains a separate observation.

## Updating without duplicating settings

The official tool version, annotated tag SHA and underlying commit SHA are held
once in `actions-lock-tool.json`. Both generation and verification call
`scripts/install_actions_lock.py`, which validates that manifest and verifies
the upstream objects before installing the release. Update the three reviewed
source values and the reviewed Linux amd64 binary SHA-256 together in one PR; no repository-variable settings need a separate edit.
Both CI paths are triggered when the manifest or installer changes.

The pilot remote is derived from `${{ github.server_url }}` and
`${{ github.repository }}` through an environment variable, so renaming or
copying the repository does not require changing a literal clone URL.
Runtime paths continue to use `${{ runner.temp }}`. Action `uses` references stay
literal because they participate in static resolution and dependency review.

The installer additionally hashes the installed `linux-amd64` executable before
any `gh actions-lock` invocation. A missing binary, digest mismatch, unsupported
platform, or installation failure stops the job. The digest in the manifest was
reviewed from the official immutable v0.4.1 release asset (9,416,866 bytes):
https://github.com/github/gh-actions-lock/releases/tag/v0.4.1 . Git object checks
alone do not authenticate release binary bytes; both gates are required. This
byte pin is not a claim of a reproducible build or independent source attestation.
