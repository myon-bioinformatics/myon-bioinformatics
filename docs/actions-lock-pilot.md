# Actions dependency lockfile pilot

The read-only `Actions lockfile pilot` workflow checks that the v0.2.0 release
tag resolves to `8030edf41a48291d48ac67090ab27e9949225de2`, then installs
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
  version comments. Do not weaken it or rewrite live workflows to tags as part
  of this experiment. If the generator rewrites those references, resolve that
  policy difference deliberately before copying the candidate into the repository.
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
- https://github.com/github/gh-actions-lock/tree/8030edf41a48291d48ac67090ab27e9949225de2
