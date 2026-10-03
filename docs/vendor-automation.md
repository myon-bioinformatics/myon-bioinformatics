# Shared vendor placement and update proposals

`vendor_sync.py` is a single-file, stdlib-only tool. Consumers keep an explicit
`vendor-lock/1` allowlist; there is no automatic directory discovery or dependency
resolver. Include LICENSE files alongside code. A commit is always a full SHA.
`ref` is used only when proposing an update, never by ordinary placement/checks.

Each file has `repository` (owner/repo), `ref`, `commit`, `source`, `destination`,
`blob_sha` (Git blob SHA-1), and `sha256`. Paths are relative POSIX paths. One
repository/ref is resolved once per update, so related files share the same
candidate commit. Unrelated upstream commits with unchanged selected bytes do
not churn existing pins. Group coupled contract/generator files under the same
repository/ref. Include every imported upstream sibling explicitly.

```json
{
  "schema": "vendor-lock/1",
  "files": [
    {
      "repository": "myon-bioinformatics/xprobe",
      "ref": "main",
      "commit": "326acd667e13b21bf53ccc1590af960edf8cbf6c",
      "source": "scripts/xprobe_pytest.py",
      "destination": "vendor/xprobe_pytest.py",
      "blob_sha": "70fac53151e97f5f28d23f9961d3837b7a885ea8",
      "sha256": "c0ea71f9d971bf47a9f5f1794ef8a7f641dd17694ea8e7f29795d326c5229285"
    }
  ]
}
```

This minimal schema example is not a complete consumer manifest: add the upstream
LICENSE with its independently verified hashes before adoption.

## Commands

```sh
python vendor_sync.py --help
python vendor_sync.py check --root . --manifest vendor.lock.json
python vendor_sync.py materialize --root . --manifest vendor.lock.json
python vendor_sync.py update --root . --manifest vendor.lock.json
```

`--help` exits before filesystem/network activity. `check` is offline and fails
on missing or mismatching files. `materialize` reuses verified local copies or
fetches raw bytes at the locked commit, verifies both hashes, then places them.
It never resolves `ref`. `update` resolves the explicit upstream refs and checks
GitHub file metadata against downloaded Git blob hashes, records SHA-256, then
places the candidate files and updates the lock. It first checks every current
file against the old lock, rejecting missing files or local edits before any
network call. Use `materialize` explicitly if restoring missing/incorrect locked
copies is intended; updates never silently repair local edits. Both write modes validate the
whole download batch before writing; replacements are atomic per file, not a
filesystem transaction. Run updates in a disposable clean checkout. Network,
missing-file and verification failures are nonzero (CLI exit 2).

Existing file permissions are retained; new files default to 0644. Manifest
collisions and duplicate destinations are checked case-insensitively.
The updater never imports candidate code, invokes Git, or installs runtime
dependencies. Upstream selection is not a compatibility guarantee: ordinary
consumer CI evaluates the update PR. Files over 8 MiB are rejected; the scope is
small public shared Python modules and their licenses, not models or packages.
Only **public upstream repositories** are supported: raw downloads are anonymous;
`GH_TOKEN` authenticates metadata calls only and does not enable private-source
placement. Slash-containing refs are encoded as a `sha` query parameter on the
lightweight commit-list endpoint (`per_page=1`), which omits commit patches;
an empty result is an explicit error.

## Scheduled updates without copying the updater into every consumer

`.github/workflows/reusable-vendor-update.yml` accepts a lock and an immutable
`tool-commit`. Consumers must call the workflow at the **same full commit** as
`tool-commit`; the workflow validates the input's full-SHA format but cannot
enforce equality with the caller's `uses` ref. Review/update the pair together.
Consumers schedule
it (for example weekly), plus expose `workflow_dispatch`. The caller should use
`permissions: contents: read` and pass `secrets.update-token` explicitly. The
workflow checks out the consumer's default branch and the pinned tool separately,
prepares the candidate, commits only reported changed paths, and creates a PR.
It only runs for schedule/dispatch, never PR events. It neither force-pushes nor
merges. The branch digest includes the current base SHA and the entire staged
Git tree, distinguishing candidates with different placed bytes and allowing
updates after base changes/reverts. Before reusing an existing remote branch,
its fetched tree must equal the verified local candidate tree; a mismatch fails
before any PR API call. Deduplication checks open PRs only. A previously closed
candidate may be proposed again; branch/PR history is never force-rewritten.
Tool updates themselves
remain a separate infrastructure pin change rather than self-updating executable
bootstrap code.

Provide a GitHub App installation token or fine-grained PAT with consumer
contents/PR write permissions. The default `GITHUB_TOKEN` does not provide the
same automatic downstream CI triggering: see [GitHub's token event guidance](https://docs.github.com/en/actions/concepts/security/github_token).
Do not silently fall back to it and assume the candidate was tested. This PR
provides the reusable mechanism; it does not configure secrets or activate a
consumer schedule. Existing PR checks and branch protection remain authoritative.
Automatic merge is not enabled by this rollout.

## Current-main audit, 2026-10-03

Read all eleven supplied repositories' main trees and open PRs. No vendor-update
automation PR was present. Provenance uses several existing shapes; migrate each
consumer's provenance tests/readers deliberately to the lock, rather than
guessing field aliases or overwriting legacy provenance behind their back.

| Repository | Existing copies / current overlap | Rollout order |
| --- | --- | --- |
| yourself | vendor/xprobe_pytest + LICENSE; JUnit #7 merged; thin-main #5 separate | First small placement/schedule pilot |
| nvd_nist_known_vulns | Same adapter provenance shape as yourself | Reuse tested pilot |
| convert_img_fmt_to_webp-CUI- | Adapter under scripts; different provenance fields | Adapt paths and provenance checks |
| ascii_artist | Five shared metadata/resolver/inspector modules; JUnit #27 merged | Group related source files |
| markdown | Same five modules; JUnit #88 merged; identity #86 open | Avoid overlap with #86 |
| mcp-toolcall-lab | Seven vendor modules; JUnit #95 open | Preserve test-only boundary; coordinate #95 |
| Ironmate | vendor modules plus root provenance helper; identity #57 open | Avoid overlap with #57 |
| browser-test-kit | scripts/git_inspector; migration #36 and bridge #37 open | Wait for source-pin changes to settle |
| web-ui | tool/vendor grouped metadata provenance + LICENSE | Preserve grouped-source contract |
| xprobe | No vendored downstream module in current main | Upstream source, no redundant consumer lock |
| myon-bioinformatics | Shared inspector/workflow source, no vendor directory | Own updater and reusable workflow |

The older JUnit report's missing `artifact-pattern` concern is already addressed
in yourself's merged #7 (`test-report-*`). A commit looking old does not by itself
prove byte drift: xprobe #7 changed tests/docs/CI, not the adapter source. The
child `-c os.devnull`/rootdir concern and CLI/import/README issues remain separate
checks; this automation PR does not claim they are all resolved.

For each consumer: adopt a complete lock preserving current bytes first, migrate
provenance checks, verify offline local tests, then wire CI placement and schedule
to a reviewed shared-tool commit. Test update proposals and consumer CI before
considering any explicit automatic-merge policy. Current deployment/Pages and
runtime dependency policies are unchanged.
