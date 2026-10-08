# Shared public vendor placement and CI updates

`vendor_sync.py` is a single-file, stdlib-only tool. Consumers keep an explicit
`vendor-lock/1` allowlist; there is no automatic directory discovery or dependency
resolver. Include LICENSE files alongside code. A commit is always a full SHA.
`ref` is used only by `update`/`promote`, never by locked
`materialize`/`check`/`enroll`.

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
python vendor_sync.py enroll --root . --manifest vendor.lock.json
python vendor_sync.py materialize --root . --manifest vendor.lock.json
python vendor_sync.py update --root . --manifest vendor.lock.json
python vendor_sync.py promote --root . --manifest vendor.lock.json
python vendor_sync.py evidence --root . --manifest vendor.lock.json --runtime-evidence vendor-promotion.json
```

`--help` exits before filesystem/network activity. `check` is offline and fails
on missing or mismatching files. `materialize` reuses verified local copies or
fetches raw bytes at the locked commit, verifies both hashes, then places them.
It never resolves `ref`. `update` resolves the explicit upstream refs and checks
GitHub file metadata against downloaded Git blob hashes, records SHA-256, then
places the candidate files and updates the lock. It first checks every current
file against the old lock, rejecting missing files or local edits before any
network call. Use `materialize` explicitly if restoring missing/incorrect locked
copies is intended; updates never silently repair local edits. `materialize`,
`update` and `enroll` validate the whole download batch before writing;
placements are atomic per file, not a
filesystem transaction. `promote` is the explicit reviewed-baseline operation:
it uses the same update resolver, verifies the promoted baseline offline, and
prints a deterministic `vendor-promotion/1` receipt containing old/new commit,
Git blob SHA and SHA-256 identities. Promotion backs up the locked manifest and
all locked destination bytes and rolls them back if update or verification
fails. It never commits, pushes, opens a PR, or writes to GitHub. Run updates in a disposable clean checkout. Network,
missing-file and verification failures are nonzero (CLI exit 2).

### Enrolling new destinations

Use `enroll` after adding reviewed exact commit/blob/SHA-256 entries, including
the source's LICENSE, to a consumer lock. The complete lock and destination
paths are validated first. Every existing destination must already match its
locked bytes; a mismatch fails before any network call or placement. Only the
destinations missing at this initial check are selected for download, at their
locked commits. Enrollment never resolves `ref`, rewrites the lock, repairs a
local edit, or writes to GitHub. Repeated enrollment is a no-op.

All missing downloads and both digests are verified before placement, then
existing destinations are checked again. A file created during downloads is
left untouched if exact; a mismatch aborts before any new placement. Each new
file is staged and installed using a hard link, so a destination created even
at the final installation boundary cannot be overwritten. An exact file that
appears at that boundary is a no-op; a mismatch is an error. The destination
filesystem must support hard links; there is no overwrite fallback. Enrollment
finishes with ordinary offline `check`, and `changed_paths` lists only the
destinations this invocation created.

This is a verified download batch, not a filesystem transaction. An I/O error
during placement, or a conflicting file created after the final batch check,
can leave earlier verified new files installed. After resolving the error,
rerun `enroll`: completed exact files are retained and only missing files are
selected. Use an isolated checkout; concurrent changes to directories or the
manifest are unsupported. Observed destination and parent symlinks are rejected
before placement.

For a newly enrolled consumer, run `enroll` and then `check` before its usual
locked/candidate test sequence. Pin a canonical tool revision that supports
`enroll`; an initial `check` alone must still reject missing destinations.

For locked `materialize` and `enroll`, raw HTTP 403/429 also uses the anonymous public Git
fallback at the exact locked commit. Snapshots are cached by repository and
commit, so mixed historical pins remain independent. Returned commit/blob and
downloaded SHA-256 must match the existing lock; the lock is never rewritten.
Enrollment's fallback reads only sources selected as initially missing; already
verified local files are excluded from the snapshot request.
Other HTTP errors remain failures, and the whole batch is verified before writes.

Existing file permissions are retained; new files default to 0644. Manifest
collisions and duplicate destinations are checked case-insensitively.
The updater never imports candidate code or installs runtime dependencies. Upstream selection is not a compatibility guarantee: consumer CI evaluates the candidate in its disposable checkout. Files over 8 MiB are rejected; the scope is
small public shared Python modules and their licenses, not models or packages.
Only **public upstream repositories** are supported. Raw downloads and metadata
calls are anonymous; no environment token is read or sent. Anonymous API rate
limits apply to updates. HTTP 403/429 switches to anonymous public Git fetch
in a temporary bare object store, reading the same allowlisted regular files
and verifying their Git blob identities. Git must be available for this path
(as it is on Actions runners). User credential helpers/config are disabled.
If metadata already resolved a full SHA, the Git fetch uses and verifies that
exact SHA. No consumer Git changes or remote writes occur. Other download
errors, failed Git reads and digest mismatches remain nonzero; old bytes are
never substituted to make CI pass. Slash-containing refs are encoded as a `sha` query parameter on the
lightweight commit-list endpoint (`per_page=1`), which omits commit patches;
an empty result is an explicit error.

## CI-only updates

Consumer CI first uses `check` and `materialize` at the recorded full commits,
then runs `update`, `check` and the existing tests automatically. No human
manual-dispatch step is required. An ALM agent can use the same CLI sequence in
its checkout. A dispatch input `vendor-mode` may select `locked` for an explicit
baseline run; ordinary push/PR events and default dispatches use `update`.
Resolve each allowlisted upstream ref once per command and record the resulting
full SHA/blob/SHA-256 before testing. The executable updater itself stays pinned.

Updates modify source/license/lock files only in the disposable CI checkout.
They do not persist to main, create branches, push, open PRs or auto-merge. The
former reusable PR-proposal workflow and dedicated token/enable-flag contract
are removed. No additional token or repository secret is required. CI checkout
permissions remain read-only, and vendor tool checkouts disable persisted Git
credentials. Shared-tool pin changes remain explicit infrastructure changes.

## Current-main audit, 2026-10-03

Read all eleven supplied repositories' main trees and open PRs. No vendor-update
automation PR was present. Provenance uses several existing shapes; migrate each
consumer's provenance tests/readers deliberately to the lock, rather than
guessing field aliases or overwriting legacy provenance behind their back.

| Repository | Existing copies / current overlap | Rollout order |
| --- | --- | --- |
| yourself | vendor/xprobe_pytest + LICENSE; JUnit #7 merged; thin-main #5 separate | First small placement/CI update pilot |
| nvd_nist_known_vulns | Same adapter provenance shape as yourself | Reuse tested pilot |
| convert_img_fmt_to_webp-CUI- | Adapter under scripts; different provenance fields | Adapt paths and provenance checks |
| ascii_artist | Five shared metadata/resolver/inspector modules; JUnit #27 merged | Group related source files |
| markdown | Same five modules; JUnit #88 merged; identity #86 open | Avoid overlap with #86 |
| mcp-toolcall-lab | Seven vendor modules; JUnit #95 open | Preserve test-only boundary; coordinate #95 |
| Ironmate | vendor modules plus root provenance helper; identity #57 open | Avoid overlap with #57 |
| browser-test-kit | scripts/git_inspector; migration #36 and bridge #37 open | Wait for source-pin changes to settle |
| web-ui | tool/vendor grouped metadata provenance + LICENSE | Preserve grouped-source contract |
| xprobe | No vendored downstream module in current main | Upstream source, no redundant consumer lock |
| myon-bioinformatics | Shared inspector/workflow source, no vendor directory | Own public placement/update tool |

The older JUnit report's missing `artifact-pattern` concern is already addressed
in yourself's merged #7 (`test-report-*`). A commit looking old does not by itself
prove byte drift: xprobe #7 changed tests/docs/CI, not the adapter source. The
child `-c os.devnull`/rootdir concern and CLI/import/README issues remain separate
checks; this automation PR does not claim they are all resolved.

For each consumer: adopt a complete lock preserving current bytes first, migrate
provenance checks, verify offline local tests, then wire CI placement and automatic updates
to a reviewed shared-tool commit. Verify locked and candidate test runs. Current deployment/Pages and
runtime dependency policies are unchanged.


The offline `evidence` mode derives `vendor-evidence/1` locked/candidate paths from the validated lock and manifest. Generated runtime receipts are passed with repeatable `--runtime-evidence` and remain separate; path collisions fail. This operation does not access the network or require receipt files to exist.


## Canonical artifact staging (#44)

`vendor_stage.py` lives beside `vendor_sync.py` in the parent repository. Fetch
both via a full-commit pinned checkout and invoke the shared script directly:

```sh
python -S .vendor-sync-tools/vendor_stage.py --root . \
  --manifest vendor.lock.json --kind candidate --output build/vendor-evidence \
  --promotion-receipt vendor-promotion.json \
  --legacy-evidence tool/vendor/provenance.json
```

Upload the generated directory. The lock alone defines locked/candidate membership;
adding an enrolled source or license requires no staging path-list edit. `kind`
labels the selected snapshot and does not certify an update or test outcome.
`--runtime-evidence` names required supplemental files; `--promotion-receipt`
includes an optional file only when present in a candidate run (omitted for locked
runs). A failed or incomplete receipt remains diagnostic bytes, not a successful
promotion claim. `--legacy-evidence` is an explicit consumer-specific projection
path, never a second list of canonical vendor members. The stager copies existing
legacy output; it does not infer, regenerate, or certify a consumer's legacy format.

The generated `vendor-evidence.json` retains `vendor-evidence/1` locked, candidate,
and runtime arrays, and adds `kind`, `legacy`, and a `sha256` map for every copied
file. Legacy files are classified separately, and runtime/legacy paths cannot
collide with lock members or the generated manifest. Receipt and legacy files
remain outside the checked-in lock. Required inputs, regular-file paths, byte
identities, case-insensitive collisions, path ancestry, and output paths are checked
before creating the directory. Existing output, symlink components (including
in-root and dangling links), traversal, metadata-name collisions, and source/output
overlap are rejected. Source bytes are read once, verified against the lock, and
then copied; a copy failure removes the partially staged directory. The command is
offline, stdlib-only, and never imports vendor modules. Use an isolated checkout;
concurrent directory/manifest changes are unsupported, as in `vendor_sync`.

## Recommended baseline catalog (#46, first phase)

`vendor-catalog.json` is a separate, explicit recommendation, not a consumer
lock or an alias for development main. `vendor_catalog.py` (stdlib-only, beside
canonical `vendor_sync.py` in this repository) provides read-only operations:

```sh
python vendor_catalog.py validate
python vendor_catalog.py compare --lock vendor.lock.json --consumer owner/consumer
python vendor_catalog.py verify
```

`validate` and `compare` are offline. `verify` reads public Git objects into a
throwaway bare repository through `vendor_sync.inspect_source`; it requires Git.
It verifies the full resolved commit, regular-file tree mode and Git blob against
retrieved bytes, and computes SHA-256. It never imports the source. Output is JSON;
invalid input or retrieval errors exit 2. No command places consumer files,
rewrites locks, promotes, commits, pushes, or creates PRs. Verification is identity
evidence, not candidate compatibility CI or an import-safety assessment.

### Contract: vendor-catalog/1

The top-level fields are exactly `schema` and nonempty `tools`. Each tool has
`repository` (`owner/repository`), `commit` (40 lowercase hex characters), and
nonempty audit `reason`. Optional `source` overrides the default `<repository>.py`
for real exceptions; optional `consumers` records per-consumer policy exceptions
or explicit enroll decisions. Unknown fields and duplicate repository/source
pairs are rejected. Different sources from the same repository are allowed.
A future incompatible contract needs a new schema version.

```json
{
  "schema": "vendor-catalog/1",
  "tools": [{
    "repository": "owner/shared_helpers",
    "commit": "1111111111111111111111111111111111111111",
    "source": "helpers/standalone.py",
    "reason": "Illustrative override, not a live recommendation",
    "consumers": {
      "owner/unused_consumer": {
        "enrollment": "enrolled", "reason": "safe_unused_copy"
      },
      "owner/incompatible_consumer": {
        "enrollment": "skipped", "reason": "requires_external_package"
      }
    }
  }]
}
```

Absent consumer decisions default to `enrolled` with
`reason: default_single_file_policy`. Both explicit decision types require a
machine-readable lowercase snake_case reason. Enrollment here is **policy**,
not proof of placement, runtime use, safety, or passing tests. Catalog maintainers
must assess the Issue #46 inert single-file/stdlib threshold before publishing a
recommendation; unused alone is not a skip reason. Skip neither removes existing
locked entries nor hides their comparison. LICENSE remains separately and
explicitly locked under the existing vendor rules; this catalog is not a complete
placement manifest. Source overrides do not infer source renames in old locks.

Comparison matches repository (case-insensitive) and exact source path, retaining
all matching lock entries including their destinations, commit/blob/SHA-256 and
ref. `missing`, `at_recommended`, and `different` describe commit pin equality
only, without assuming ancestry or claiming byte parity. Unrelated entries such
as LICENSE are outside that tool's comparison. Even identical source bytes at
different commits report `different`; malformed locks fail existing
`vendor_sync.validate` unchanged. Consumer locks remain authoritative for all
existing materialize/check/enroll operations.

Initial recommendations deliberately retain the parent's existing gh_identity
pin and the previously adopted xprobe JUnit bridge baseline, not today's moving
main. A later recommendation is a reviewed catalog commit. Development heads,
consumer locks, and catalog commits advance independently. Candidate CI,
compatibility receipts, automatic PR creation and coordinated promotion are
future phases; this first phase does not change existing CI update behavior.
