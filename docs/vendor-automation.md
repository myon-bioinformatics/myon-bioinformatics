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

An optional `bundle` object (`minpy-bundle/1`) may be added to a file entry
without changing existing entries. It records that `source` is a single-file
bundle produced by `tools/minpy.py`: `tool`, `tool_version`, `entry`,
`search_paths`, `allow`, and `allow_cycles`. `vendor_sync.py check` still
verifies the locked bytes. `python tools/minpy.py check` regenerates the
bundle from the entry at the locked commit and compares it. See `docs/minpy.md`.

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

## Central consumer topology

`vendor-consumers.json` is the parent-owned machine-readable registry of
cross-repository vendor placement intent. It answers **which repository consumes
which upstream source at which destination**. It deliberately does not repeat
commit, Git blob, or SHA-256 identity: the consumer's `vendor-lock/1` remains the
authority for the exact bytes actually adopted.

Source/consumer is a relationship per artifact, not a permanent repository
classification. A repository can own one shared artifact and consume another.
A repository must not enroll its own canonical artifact merely to make layouts
uniform. For example, `xprobe` owns `xprobe.py`; downstream repositories may
enroll it, but xprobe does not create a redundant self-consumer entry for
`xprobe.py`.

The registry uses `vendor-consumers/1`:

- `repository`: the consumer repository.
- `state: locked`: the relationship is represented by the listed `lock` path
  and its topology should match the registry.
- `state: legacy`: a known checked-in vendor relationship predates
  `vendor-lock/1`; `lock` is null and a machine-readable `reason` plus the
  existing `legacy_provenance` paths are recorded until migration.
- `entries`: `repository`, `source`, and `destination`, plus an optional
  `bundle` object when the source is a minpy bundle (same `minpy-bundle/1`
  shape as the lock; no commit or digest). Exact commit/ref/blob/SHA-256
  values stay in each consumer lock. A bundle metadata mismatch is
  `bundle_mismatch` and fails comparison. `html-import-vendor/1` sources may
  carry the same optional `bundle` object; sources without it are unchanged.
- Duplicate consumers or destinations, unsafe paths, and self-vendor entries are
  rejected.

The current registry includes the parent itself plus every repository found with
a `vendor-lock/1` manifest in the cross-repository audit. It also records
`search_seq_including_spaces` as a legacy xprobe consumer so the absence of a
lock is explicit rather than silently omitted.

Validate the central topology, or compare one checked-out consumer lock without
network access:

```sh
python -S vendor_consumers.py validate
python -S vendor_consumers.py compare \
  --consumer myon-bioinformatics/yourself \
  --lock path/to/yourself/vendor.lock.json
```

The CLI exits 0 for a match (or valid registry), 1 for topology drift, and 2
for input/schema/I/O errors. Drift still emits the full comparison JSON.

A comparison checks only repository/source/destination topology. It never
promotes a recommendation or treats the parent registry as evidence of the exact
bytes in a consumer checkout.

Keep the three ownership layers distinct:

1. `vendor-consumers.json` — intended consumer/source/destination topology.
2. `vendor-catalog.json` — reviewed recommended upstream baselines and
   enrollment policy.
3. Consumer `vendor.lock.json` — exact adopted commit/blob/SHA-256 identity.

Changing one layer does not silently rewrite either of the others. Consumer
migration and promotion remain explicit reviewed operations.

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

### Node launcher for vendored Python CLIs (`py_launch.cjs`)

Parent-owned `py_launch.cjs` is a single CommonJS module (Node >= 24, `node:`
builtins only) that spawns a stdlib Python script with argv passthrough, inherited
stdio, and exit-code forwarding (exit code 2 on spawn failure, missing
interpreter, signal, or a Node older than 24). A signal prints its name on
stderr. Set `jsonErrors: true` or `PY_LAUNCH_ERROR_FORMAT=json` to emit one
stderr object `{ok:false, code, message, hint, exit_code}` for those launcher
failures. The switch does not read argv, so the child tool keeps its own
`--json`. Library callers use `run(scriptPath, args, { python,
input, timeout, maxBuffer, maxInputBytes, responseMaxBytes, env, noSite, isolated, envVar })`
with `spawnSync`, surfacing stderr on failure. `maxBuffer` and `responseMaxBytes`
are mutually exclusive; `env` merges over `process.env` without injecting
`PYTHONUTF8` / `PYTHONIOENCODING`. Requires Node 24+. Interpreter resolution
order: explicit `python`, `process.env[envVar]`
(for example Ironmate's `IRONMATE_PYTHON`), `PYTHON`, `python3`, `python`, and
on Windows `py -3`. Python older than 3.9 is rejected with a clear message (exit
2). Explicit `python` or a set `envVar` rejects Windows `.cmd`/`.bat` paths (exit 2).
Non-strict `PYTHON` pointing at a batch shim is skipped with a warning so later
candidates (`python3`, `python`, `py -3`) can run. `-S` is optional per tool via
`noSite: true`
(default off). `-I` is optional via `isolated: true` (default off). CPython
treats `-I` as `-E` plus `-s` plus `-P`: every `PYTHON*` environment variable is
ignored (including `PYTHONPATH` set through `env`), user site is disabled, and
the script directory is not placed on `sys.path`, so sibling-module imports fail
unless the script inserts its own directory (for example
`sys.path.insert(0, os.path.dirname(__file__))`, as Ironmate `flow_workspace`
did when invoking `python -I -S` directly).

Add the `vendor-catalog.json` recommendation only after the launcher is on
default branch (merge commit pin), then enroll with `vendor_sync.py` like any
other catalog source (`destination`: `vendor/py_launch.cjs`, plus `blob_sha`
and `sha256` from `inspect_source` or `vendor_catalog.py verify`). Thin
consumer wrapper (three lines):

```js
"use strict";
const { main } = require("./vendor/py_launch.cjs");
if (require.main === module) main(__dirname + "/your_tool.py", { envVar: "IRONMATE_PYTHON", noSite: true });
```

Optional `package.json` `"bin"` can point at that wrapper for `npx
github:owner/repo#commit` style invocation; the vendored launcher stays shared.

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
| browser-test-kit | browser-test and evidence scripts; gh_ops removed in favor of parent | Coordinate consumer lock updates with parent registry |
| web-ui | tool/vendor grouped metadata provenance + LICENSE | Preserve grouped-source contract |
| xprobe | Parent LICENSE snapshot only (`vendor/myon-bioinformatics-LICENSE`) | Upstream source of `xprobe.py`; locked consumer of the parent LICENSE only, no self-consumer entry |
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

Consumer-held catalog snapshots (for example copied into a child repository) may
call `validate --allow-legacy-implicit-source` until **2027-04-01** to accept
omitted `source` fields using the retired `<repository>.py` default; each
omission emits a `DeprecationWarning`. After that date the flag is ignored and
omitted `source` is always an error. The parent `vendor-catalog.json` must
always declare `source` explicitly.

`validate` and `compare` are offline. `verify` reads public Git objects into a
throwaway bare repository through `vendor_sync.inspect_source`; it requires Git.
It verifies the full resolved commit, regular-file tree mode and Git blob against
retrieved bytes, and computes SHA-256. It never imports the source. Output is JSON;
invalid input or retrieval errors exit 2. No command places consumer files,
rewrites locks, promotes, commits, pushes, or creates PRs. Verification is identity
evidence, not candidate compatibility CI or an import-safety assessment.

### Contract: vendor-catalog/1

The top-level fields are exactly `schema` and nonempty `tools`. Each tool has
`repository` (`owner/repository`), `commit` (40 lowercase hex characters),
`source` (relative POSIX path to the recommended file), and nonempty audit
`reason`. Omitted `source` is rejected; there is no repository-name default.
Each repository name must normalize to a valid Python identifier by lowercasing
and replacing `-` and `.` with `_`, rejecting Python keywords; two different
repositories (even under different owners) whose names normalize to the same
module identity are rejected so independent `vendor/<module>.py` paths cannot
collide. Repository strings that differ only by case refer to the same consumer
and do not trigger a normalization collision. A stdlib min artifact is a second
tool on the same repository, with `source` `<stem>_min.py`, not a second
repository (normalized repository module names therefore do not collide).
Optional `consumers` records per-consumer policy exceptions or explicit enroll
decisions. Unknown fields and duplicate repository/source pairs are rejected.
The recommendation unit is an independent source artifact,
identified by repository/source, rather than an entire repository release.
Different sources from the same repository are intentionally allowed for
standalone helpers; each carries its own explicit recommended commit. This does
not express coupled-file dependencies, a shared-commit constraint, compatibility
between sources, or atomic multi-file promotion. Coupled artifacts need a future
explicit grouping contract; multiple entries alone must not imply such guarantees.
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
Changing a source path, even at the same commit, intentionally reports `missing`
when no lock entry matches that new repository/source. Treat it as a new artifact:
explicitly review its source identity, LICENSE and destination before enrollment.
The old lock entry remains independent; this read-only comparison neither maps
the old source to the new one nor removes or rewrites it.

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
fleet compatibility receipts, automatic PR creation and coordinated promotion
remain future phases; the candidate CI connection below does not change existing
development-head CI update behavior.

## Recommended candidate CI (#46, second phase)

`vendor_candidate.py` reuses the catalog, consumer registry and canonical
`vendor_sync` Git snapshot/digest/check operations. The first connected consumer
is this parent repository; the proving artifact is `gh_identity.py` and its
explicitly locked LICENSE. `.github/workflows/vendor-candidate.yml` automatically
runs on relevant PRs/main pushes, and supports manual dispatch, on development/test Python 3.11
and 3.14 with read-only permissions and no persisted checkout credentials.

```sh
python -S vendor_candidate.py --root . \
  --consumer myon-bioinformatics/myon-bioinformatics \
  --repository myon-bioinformatics/gh_identity --source gh_identity.py \
  --test tests/test_gh_ops.py --output ../candidate-results
```

The output directory must be new, outside the consumer root and without symlink
components. The registered lock topology and all locked source bytes are checked
before fetching. The catalog selects a full commit, independently of `ref` or
upstream main. Source and LICENSE are fetched together from that exact Git
snapshot; resolved commit, regular-file mode, Git blob and SHA-256 are checked
before placement. Candidate metadata uses the verified source bytes, never an
unverified catalog digest. Files and a temporary candidate lock are placed only
in a disposable copy of the consumer. Other locked artifacts retain their pins.
The original consumer lock, source files, catalog and registry are never written.

Tests are explicit trusted consumer Python paths supplied by the workflow/operator;
they are not catalog-provided commands. `tests/test_gh_ops.py` exercises the actual
parent gh_identity loader and its callers, rather than testing only upstream code.
Candidate integrity is checked again after tests. This copy isolates accidental
relative writes; it is **not a security sandbox** for malicious tests. Concurrent
input changes and coupled multi-source recommendation groups are unsupported.

`verification.json` uses `vendor-candidate-verification/1`:

- `success` / `candidate_compatible`: identity checks and selected tests passed,
  with nonempty JUnit and no skipped cases. This is compatibility evidence only
  for the named tests and Python environment, never promotion authorization.
- `mismatch`: locked/candidate identity mismatch or test failure. The original
  lock remains unchanged.
- `unverified`: retrieval/runner/input failure, explicit skip, missing enrollment,
  absent tests, missing/invalid/empty JUnit, or skipped cases. Never treated as a pass.

The receipt records the consumer/tool, selected recommended commit, original lock
SHA-256, normalized catalog/registry SHA-256, locked and candidate file identities,
selected test paths, test exit status/output, JUnit SHA-256 and `promoted: false`.
Successful CLI exit is 0; all other outcomes exit 2. Candidate JUnit is retained
when produced, including failed tests. Actions uploads diagnostic JSON/JUnit even
when verification fails, plus the offline verifier regression JUnit. The artifact's
workflow run/head provides the consumer revision identity. Output creation errors
exit before verification; otherwise input errors produce an unverified receipt.

This phase neither updates recommendations nor promotes/commits/pushes/opens PRs
from CI. Baseline recommendation remains a separate reviewed catalog change.
Fleet fan-out, consumer-specific test selection contracts, reusable compatibility
receipt validation, release-train aggregation and coordinated promote remain
follow-up work. Existing development-head CI update behavior is unchanged.

### First live candidate result

On 2026-10-11 the parent lock used gh_identity `c950ed3882f05668575b7298a67bc811e9cf925a`,
while the catalog recommended `fc2c527257b12eb99c00637bbae74f8988fd6bf4`.
Exact candidate retrieval/digests passed, but the parent tests produced 123 passed
and one failure: `test_default_open_prs_uses_bounded_canonical_search` needs
`gh_identity.search`, absent from the recommended revision. The receipt reports
`mismatch` / `tests_failed`; the candidate Actions job is intentionally nonzero
until a separate reviewed recommendation or consumer compatibility change.
Do not suppress this test, advance the catalog implicitly, or promote this candidate.

### Explicit compatible recommendation

The catalog now explicitly recommends gh_identity
`c950ed3882f05668575b7298a67bc811e9cf925a`, the already installed parent baseline
that provides the required canonical search API. This is a reviewed recommendation
change, not automatic adoption of the upstream development head. The runtime
`vendor.lock.json` and vendored source/LICENSE remain unchanged.

`docs/vendor-catalog-verification.json` and the initial coverage report are historical
snapshots of the earlier catalog, not verification receipts for this recommendation.
Current compatibility evidence comes from the candidate workflow for this commit.

Equal recommended and installed commits do not skip verification: source and LICENSE
are still fetched from the exact commit, their identities checked, and the real
consumer tests run in the disposable copy. Fetch or test failures remain non-success.

## Read-only fleet registration plan (#46)

`vendor_fleet_plan.py` combines the reviewed recommendation catalog and
parent-owned consumer topology into a **read-only** registration worklist:

```sh
python -S vendor_fleet_plan.py --consumer myon-bioinformatics/Ironmate
python -S vendor_fleet_plan.py
```

It emits `vendor-fleet-plan/1` JSON with one row per selected consumer and
catalog tool: `registered`, `needs_registration`, `skipped`, or
`legacy_consumer_review`. A missing registration has a null destination:
the planner does not invent a placement path or copy any bytes. Skips include
their explicit policy reason, and an existing enrolled relationship is
reported even if a newer catalog default skips it. An optional repeatable
`--consumer` limits the report to known registered consumers.

This is **not** a replacement for `vendor_consumers.py compare` (which
compares an actual lock), `vendor_catalog.py compare` (which compares
recommended commit pins) or `vendor_sync.py check` (which verifies bytes).
`candidate_compatibility: unverified` is always explicit: a registration
plan never means tested, safe to promote, or fully installed. Only the
existing registered fleet is covered; discovering new repositories remains
a separate inventory task. The tool makes no network calls, imports no
candidate source, and performs no filesystem writes.

Parent CI automatically emits the read-only JSON report as the
`vendor-fleet-plan` artifact when the catalog/registry/planner changes
and also supports manual dispatch. Since existing registrations may
intentionally be incomplete, this report is **advisory** and does not
silently fail the workflow for every candidate. `--fail-on-missing`
provides an explicit strict exit-1 check for a selected consumer cohort.
Canonical source artifacts owned by the consumer itself are excluded,
because self-vendoring is forbidden by the registry contract. For a new
registration request, add a reviewed catalog entry or explicit consumer
relationship in the parent; this alone does **not** mutate the child's
lock or place files. An eventual parent-led candidate PR still requires
source/LICENSE verification and the consumer's CI gates.

## Compact license references

A registry entry may use one local path instead of repeating a LICENSE entry:

```json
{"repository": "owner/tool", "source": "tool.py", "destination": "vendor/tool.py", "license": "vendor/tool-LICENSE"}
```

`vendor_consumers.validate` expands `license` to the same repository's upstream
`LICENSE` and that local destination before the existing topology checks. It does
not mutate the input. An absent locked LICENSE or duplicate/unsafe destination
still fails. Put a shared license on one source entry only. A nonstandard upstream
license filename or a license-only enrollment uses the existing explicit entry.
The separately verified `vendor-lock/1` commit/blob/SHA-256 records remain intact;
this is a shorter registry representation, not a weaker license check. MIT text
and copyright notices remain unchanged. Aoi manages subtree children in its own
child registry; the account parent remains responsible for its direct consumer
baselines. Whole-subtree synchronization and optional minpy file consumption are
separate mechanisms.
