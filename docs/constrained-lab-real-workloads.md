# Four pinned real workloads (Issue #143)

This is a reproducible comparison harness and an **incomplete exploratory
experiment**, not completion of #143 or #133. PR #144 supplied observation
scaffolding. The strict 128MiB/CPU1 four-lane demonstration remains open.
No child repositories, adopted locks, or existing workflows are changed.

## Reproduction

The manifest in `fixtures/constrained-workloads/manifest.json` freezes the four
public default-branch snapshots inspected on 2026-10-28. Stage them outside
this repository **before** the offline experiment:

```sh
python -S - <<'PY'
import json, pathlib, subprocess
manifest = json.loads(pathlib.Path('fixtures/constrained-workloads/manifest.json').read_text())
root = pathlib.Path('../staged'); root.mkdir(exist_ok=True)
for name, source in manifest['sources'].items():
    dest = root / name
    subprocess.run(['git', 'clone', '--no-checkout', source['url'], str(dest)], check=True)
    subprocess.run(['git', '-C', str(dest), 'checkout', '--detach', source['sha']], check=True)
PY
# In a dedicated Linux cgroup v2 with memory.max <= 134217728 and CPU <= 1:
python -S tools/constrained_lab_workloads.py --staged ../staged --output results.jsonl
# Explicit diagnostic mode when that cgroup is unavailable (never acceptance):
python -S tools/constrained_lab_workloads.py --staged ../staged --exploratory --output diagnostic.jsonl
PYTHONPATH=. python -S -m unittest discover -s tests -p 'test_constrained_lab_workloads.py' -v
```

Git is preparation/provenance tooling, not a measured workload dependency.
The runner never fetches. It rejects different HEADs, dirty worktrees (including
untracked nonignored files), and changed fixture bytes. Full SHA pins, input
SHA-256, seed 7, interpreter/version, kernel, libc, CPU affinity, cgroup readings,
fixed worker environment, and harness/auditor hashes are retained in JSONL.
Output files use exclusive creation to avoid overwriting prior evidence.

All workers use `python -S`; no package installation or Node bridge is needed
for these particular Python APIs. This does not test Node entrypoints or live
GitHub/MCP/browser/model workflows. One campaign schedules one task from each
repository, with pool sizes 1, 2, 4, each audited and unaudited, three repetitions.
It compares cross-repository scheduling, **not intra-repository scaling**.
There is no stochastic algorithm; seed and PYTHONHASHSEED are fixed at 7.

## Workload selection and correctness

The fixed 24-line UTF-8 corpus has 8 occurrences of `needle`; the image case uses
a fixed 32x32 RGBA pair derived from the fixture's image dimensions. These exact
inputs and fixed algorithms are repeated in every configuration.

| Repository | Real source entrypoint | Independent oracle | Scope / native alternative |
| --- | --- | --- | --- |
| gh_identity | `gh_identity.py:html_content` | Exact paragraph text, with navigation excluded | Saved HTML extraction using HTMLParser; does not replace live browser execution or verify identity |
| xprobe | `xprobe.py:search_text` | Sum of literal substring counts, 8 | Existing Python literal-search implementation; avoids grep for this narrow search contract; regex/file/encoding semantics not compared |
| Ironmate | `image_compare.py:metrics` | MAE=RMSE=10, alpha MAE=0, 0 exact pixels, 1024 pixels | Existing Python RGBA metrics; external imaging tools not needed by this callsite's implementation; perceptual/image-format equivalence not evaluated |
| mcp-toolcall-lab | `src/mcp_toolcall_lab/jsonl_log_archive.py:build_archive`, `JsonlArchive.search` | Exact `(1-based line, JSON text)` list from literal scan | Existing Python lzma/zlib/struct archive path, not external xz/grep; build failed before search in this environment |

Source inspection suggests these paths already implement the narrow operation
with standard-library functionality. This is **not** executable-trace proof.
For all four lanes the dynamic external dependency classification is
**unmeasured**. Justified, replaceable, or unexplained external executables cannot
be assigned because no exec events were observed. No external/native A/B cost
or general equivalence measurement has been performed. Python's linked native
libraries are not separate executables and are not a dependency-free claim.

## Budget, output, and observation boundaries

Normal mode fails closed unless existing `verify_budget` accepts the cgroup.
It does not configure cgroups. Workers additionally enforce RLIMIT_AS=128MiB,
RLIMIT_CPU=10 seconds, RLIMIT_FSIZE=64KiB. **Address space is not RSS**; this
conservative extra guard can reject a workload that would fit a 128MiB physical
memory cgroup. None of these per-process guards establishes an aggregate
four-worker memory budget. A future dedicated-cgroup experiment must assess
whether this additional guard is appropriate; it must not silently drop the
128MiB cgroup requirement to obtain a pass.

Optional host `libseccomp.so.2` is loaded through stdlib ctypes to deny
`socket`, `socketpair`, and `connect` in each trusted worker before importing
source. The worker self-check must receive PermissionError. Filter setup failure
prevents the workload from running. Descendants inherit the filter. Driver and
preparation are outside that filter; stdin/stdout/stderr are local or discarded,
and caller credentials are removed from the environment. This is a scoped
network control for trusted tasks, not a general sandbox or proof about
filesystem/inherited descriptors. GPU APIs are unused; devices are not isolated.
libseccomp and strace are instrumentation/control dependencies, not dependencies
of the selected source functions or the existing core runner.

The existing auditor is invoked unchanged, with 64KiB trace retention, at most
1000 returned events, 20-second audit timeout, and argv/environment redaction.
Worker output is discarded; receipts are checked against a 64KiB cap. Audit
JSON is retained only up to 64KiB and adapter output is bounded by its trace/event
caps; oversized JSON is unknown. The outer launch has a 25-second deadline and
best-effort process-group kill. The auditor starts its own session; escaped
processes may outlive the driver. Trusted selected functions do not deliberately
escape. This is not hostile-command containment.

`worker.cpu_seconds` and `wall_seconds` cover source imports and execution after
network setup; worker RSS includes startup. Linux `wait4` also records CPU and
maximum descendant RSS for each driver plus reaped descendants, including
instrumentation. RSS maxima are never summed or called aggregate peaks.
Cgroup before/after memory/current/peak/CPU readings describe the entire visible
cgroup, including unrelated activity and historical peaks; CPU deltas are not
attributed to the workload. Aggregate workload memory peak remains null.

## Measured exploratory result, 2026-10-28

The visible host cgroup allowed 8GiB and CPU quota 800000/100000 (8 CPUs), rather
than the requested 128MiB/CPU1. Cgroup creation failed read-only; `unshare -n`
failed with Operation not permitted. These measurements cannot demonstrate the
requested resource envelope. Worker network-denial checks did succeed.

Retained evidence: `docs/evidence/constrained-workloads/2026-10-28-exploratory.jsonl`.
There are 18 campaigns / 72 attempted lane executions. Every unaudited
configuration produced 3 correct lanes and the same archive-build MemoryError.
The three successful lanes matched their independent oracles and had stable
result hashes across all 9 unaudited runs. Archive failure had no result hash.
The failure is under RLIMIT_AS, not a measured cgroup OOM or proof that the
archive cannot fit 128MiB RSS.

All 36 audited attempts returned `tracing_error` with no worker receipt: the
installed strace could not use ptrace (Operation not permitted). They did not
execute the workload, and are neither passes nor valid overhead comparisons.
The audit JSON preserves unknown coverage and null external totals. Failure
launch wall/CPU numbers in JSONL are measurements of failure handling only;
do not divide them by unaudited timings to report a speedup or tracing overhead.

For reference, unaudited **incomplete campaigns** had median wall seconds
0.396260 / 0.243863 / 0.181188 for pool sizes 1 / 2 / 4 (three repetitions each).
These describe this larger host and three successes plus one failure, not
four-lane performance or CPU1 speedup. Raw worker wall/CPU/RSS and cgroup
readings are retained without filling any unavailable metric.

## Validation and remaining work

Six new stdlib regression tests pass: fail-closed budget, changed fixture,
wrong/dirty source, unavailable audit without fabricated metrics, per-child
wait4 accounting, and kernel network denial. The wider constrained-lab suite
ran 84 tests: 2 errors and 1 skip. Both errors are existing Linux RSS-sampling
assertions receiving null. The same two errors reproduce in the untouched
base `aed1930834c1ddf8788dc54e0ccbadfa64eac955` (15 budget tests); this PR does
not alter their behavior.

Remaining: dedicated 128MiB/CPU1 cgroup; review the additional AS guard; all
four workloads passing; permitted tracing and valid traced/untraced pairs;
per-campaign cgroup attribution and memory high-water; workload-level external
callsite classifications where exec events exist; current-head CI and independent
review. No merge, Issue closure, complete observation, or four-repository
acceptance is claimed.
