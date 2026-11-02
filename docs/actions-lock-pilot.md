# Actions dependency lockfile pilot

The read-only `Actions lockfile pilot` workflow checks that the v0.2.0 release
tag is the annotated tag object `8030edf41a48291d48ac67090ab27e9949225de2`
and that this object points to commit
`d5dca22a06ff8590d50b41612038996800cfb336`, then installs
the official release binary using `gh extension install --pin v0.2.0`.
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
- https://github.com/github/gh-actions-lock/tree/d5dca22a06ff8590d50b41612038996800cfb336

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
