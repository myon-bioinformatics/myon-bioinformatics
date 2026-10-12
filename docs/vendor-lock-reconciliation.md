# Consumer lock reconciliation — Issue #46 stage 3

PR #72 adds an **observation-only** parent-initiated reconciliation. The
consumer's own `vendor.lock.json` remains authoritative for adopted bytes.
This lane neither updates a child repository nor enrolls any catalog entry.

## Reproduce

```bash
python -S vendor_lock_reconcile.py \
  --consumer myon-bioinformatics/flutter_navigation_basic \
  --consumer myon-bioinformatics/mcp-toolcall-lab \
  --review docs/vendor-registration-review.json \
  --output reports/vendor-lock-reconciliation.json
```

Supply `GH_TOKEN` (or `GITHUB_TOKEN`) for authenticated GitHub API reads.
Each consumer `main` ref is resolved first to an immutable commit SHA; the
manifest is fetched at that SHA. GitHub blob identity and manifest SHA-256
are preserved in `lock_provenance`. The report does **not** fetch/reverify
every vendored source byte: `lock_file_bytes_verified: false`.

Offline usage accepts one `--lock OWNER/REPO=/path/to/vendor.lock.json`
per selected consumer. Offline reports retain the **raw input bytes' SHA-256
and Git blob SHA-1**, but cannot prove a specific consumer commit; manual
review evidence then has status `stale_or_unpinned`. Online reports also
retain the originally requested ref, immutable resolved commit and source
URL, so the comparison can be audited after consumer main moves.

`--fail-on-drift` optionally exits 1 for actual topology drift or a differing
recommended pin. This flag is intentionally **not** used by the default
report-only Actions workflow; unresolved catalog recommendations and review
candidates are not CI failures. Invalid/missing manifests or GitHub errors
fail closed (exit 2) rather than appearing as empty locks.

The `vendor-lock-reconciliation/1` JSON distinguishes:

- `topology.missing` / `topology.unexpected`: **registry vs actual lock**.
- `unregistered_candidates`: catalog policy recommends an unregistered
  tool. This is not evidence of a broken consumer.
- `explicit_skips`: policy decisions, not errors.
- `state: legacy`: separate migration review; no fabricated lock.
- `baseline_mismatches`: same source identity, a pin differing from the
  recommendation. This does not establish which revision is suitable.
- `baseline_absent`: recommendation with no matching lock pin; overlaps
  the unregistered candidates and may include a stale registry entry.
- `review`: evidence-based candidate triage, never automatic registration.

## Initial checked state, 2026-10-12

Consumer revisions: Flutter
[`782babef`](https://github.com/myon-bioinformatics/flutter_navigation_basic/commit/782babef2fed0a068f6b6885ffeb4cdb2900456c);
mcp-toolcall-lab
[`059c9456`](https://github.com/myon-bioinformatics/mcp-toolcall-lab/commit/059c9456b5fe9ef06c4749e510fe4a6f2c9c8c17).

| Observation | Flutter | mcp-toolcall-lab |
|---|---:|---:|
| Registry file entries | 18 | 14 |
| Actual lock file entries | 13 | 14 |
| Missing relative to registry | 5 | 0 |
| Unexpected relative to registry | 0 | 0 |
| Unregistered policy candidates | 6 | 7 |
| Existing pins different from recommendation | 3 | 4 |
| Recommendation not pinned | 7 | 7 |
| Explicitly skipped catalog tools | 19 | 19 |
| Technically eligible for a **scoped CI trial** | 2 | 4 |
| Requires further investigation | 4 | 3 |
| Enrollments approved or tested with candidate | 0 | 0 |

The five Flutter registry entries absent from its actual lock/files are
`Ironmate:repository_metadata_contract.py`,
`Ironmate:repository_metadata_generator.py`, `Ironmate:LICENSE`,
`myon-bioinformatics:git_inspector.py`, and
`myon-bioinformatics:LICENSE`. The first three are explicitly documented
as removed when repository metadata switched to `gh_identity` in #169.
The Flutter CI test
[`test_repository_metadata.py`](https://github.com/myon-bioinformatics/flutter_navigation_basic/blob/782babef2fed0a068f6b6885ffeb4cdb2900456c/tool/python/tests/test_repository_metadata.py)
asserts the **13-item** adopted set and exact digests. The remaining parent
files are also absent at that revision. Do not restore obsolete files to
make the parent registry green; reconcile stale registry intent separately.

## Candidate triage and consumer CI gates

`docs/vendor-registration-review.json` records one manually reviewed
observation for each of the **13** candidates, including consumer SHA,
recommended upstream SHA, proposed path, upstream LICENSE, dependency notes,
observed namespace collision and the existing consumer CI workflow.
Upstream source paths and MIT LICENSE were checked **at recommended commits**;
all inspected Python module imports use the stdlib. Runtime requirements and
operational side effects remain separate considerations.

| Candidate | Flutter | mcp-toolcall-lab | Gate |
|---|---|---|---|
| `browser_evidence.py` | Investigate overlap with `check_evidence.py` | Scoped CI trial | No known enrollment need |
| Parent `gh_ops.py` | Investigate; existing destination is *byte-identical* | Investigate; existing destination has different bytes | Preserve provenance and avoid collisions |
| `gh_workflow.py` | Investigate | Investigate | Requires external authenticated `gh` CLI |
| `nvd_nist_known_vulns.py` | Investigate | Investigate | External NVD API/network policy and use case |
| `markdown.py` | Scoped CI trial | Already registered | Confirm use case and test impact |
| `ascii_artist.py` | Scoped CI trial | Already registered | Confirm use case and test impact |
| `xprobe.py` | Already registered | Scoped CI trial | Confirm tests and JUnit adapter needs |
| `yourself.py` | Already registered | Scoped CI trial | Confirm environment diagnostics intent |
| `cli_args.py` | Already registered | Scoped CI trial | Confirm CLI wrapper intent |

The recommended parent `gh_ops.py` and Flutter's pinned
browser-test-kit `scripts/gh_ops.py` share Git blob
`6e5470e1e9395f9f3dd0908d3ce5190e0294e807` at the compared
revisions; the lab's existing pin has blob
`e4bd079300087a275c4fe577399e3230e5cd5b54`.
The same-name path must **not** be overwritten or enrolled twice merely
because the catalog lists an additional upstream identity.

Existing consumer test gates:
[Flutter Non-Dart](https://github.com/myon-bioinformatics/flutter_navigation_basic/blob/782babef2fed0a068f6b6885ffeb4cdb2900456c/.github/workflows/non-dart.yml),
[lab test.yml](https://github.com/myon-bioinformatics/mcp-toolcall-lab/blob/059c9456b5fe9ef06c4749e510fe4a6f2c9c8c17/.github/workflows/test.yml).
These were inspected, **not** run against newly installed candidate files.
The parent CI proves the read-only reconciliation, not candidate compatibility.
Any candidate promotion must go through a separately reviewed child PR and
its actual lock/materialization/pytest or platform CI.

## Downstream contract and limitations

Reports carry exact `consumer`, `lock_path`, resolved `commit`,
manifest hashes, source identity, recommended pin, proposed destination
and evidence freshness. Downstream candidate fan-out and coordinated promote
must honor `registration_approved: false` and `compatibility: unverified`.
A change to the consumer commit or recommended baseline invalidates the
review snapshot; it never silently refreshes its approval.

Actual compatibility still requires explicit consumer tests. Neither a
successful remote lock fetch nor a syntactically valid JSON report certifies
that consumer files have the pinned bytes or that a candidate is needed.
