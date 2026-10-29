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
It does not configure cgroups. Workers additionally default to RLIMIT_AS=128MiB
(explicit diagnostic choices: 192 or 256MiB),
RLIMIT_CPU=10 seconds, RLIMIT_FSIZE=64KiB. **Address space is not RSS**; this
conservative extra guard can reject a workload that would fit a 128MiB physical
memory cgroup. None of these per-process guards establishes an aggregate
four-worker memory budget. The follow-up below isolates this additional guard;
a dedicated-cgroup experiment must still assess physical-memory acceptance.
Changing the AS guard must not silently drop the 128MiB cgroup requirement.

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

Remaining: dedicated 128MiB/CPU1 cgroup; all four workloads passing in that
strict envelope; permitted tracing and valid traced/untraced pairs;
per-campaign cgroup attribution and memory high-water; workload-level external
callsite classifications where exec events exist; current-head CI and independent
review. No merge, Issue closure, complete observation, or four-repository
acceptance is claimed.


## MemoryError follow-up, 2026-10-28

Baseline examined: parent `c16b85be49ce26d5cd9e7acd80f4988e5ceb3d07`.
All four source pins, fixture bytes, oracle, archive format and source compression
settings remain unchanged. The original exploratory JSONL above is preserved.
The harness now accepts `--address-space-mib {128,192,256}` (default remains
128), propagates and records it, and retains filename/function/line traceback
frames on failure. Normal mode still requires the same cgroup budget verifier,
including when a larger **virtual-address** guard is explicitly selected.

### Failure attribution

The real fixed archive input is 1,142 UTF-8 bytes (24 records). Under AS=128MiB,
all three new unaudited runs fail at `build_archive` line 339 -> `_compress_block`
line 186 -> Python `lzma.compress` line 327. That Python line constructs
`LZMACompressor`, before `comp.compress(data)` or `comp.flush()`.
The receipt records the actual traceback, not an inferred exception location.

[Python's LZMA documentation](https://docs.python.org/3.12/library/lzma.html#lzma.LZMACompressor)
defines omitted preset/filters as preset 6. The standalone diagnostic compares
omitted preset with explicit 6, plus presets 0 and 3 as controls. Each case runs
in a fresh `python -S` process. Incremental mode retains the compressor through
initialization, compression and flush snapshots; a separate one-shot run uses
the same `lzma.compress(payload, format=FORMAT_XZ)` call as the fixed source.
No preset is substituted in the archive workload.

Retained `2026-10-28-lzma-probe.jsonl`: **128 probes** = four AS conditions
(128/192/256MiB and inherited unlimited AS), four input lengths (0/1/1024/1048576
bytes of repeated ASCII x), four presets (omitted/6/0/3), and two APIs.
Default and explicit 6 both fail **during initialization**, even with empty
input, at AS=128MiB. Their one-shot controls also fail. All 112 other cases
succeed and round-trip; successful default/6 and incremental/one-shot outputs
are byte-identical for each length. Presets 0/3 succeed at 128MiB, but are
**diagnostic controls only**, not a proposed change to the pinned source.

Representative AS=192MiB, default-preset **point samples**, in bytes:

| Input | Phase | Virtual size | RSS |
| --- | --- | ---: | ---: |
| 0 | Before constructor | 42,287,104 | 12,632,064 |
| 0 | Constructor returned | 139,976,704 | 29,814,784 |
| 0 | Flush completed, compressor retained | 139,976,704 | 29,839,360 |
| 1,048,576 | Before constructor | 43,339,776 | 13,676,544 |
| 1,048,576 | Constructor returned | 141,029,376 | 30,859,264 |
| 1,048,576 | Flush completed, compressor retained | 141,029,376 | 40,345,600 |

The empty-input constructor increases sampled virtual size by about 93.2MiB;
total virtual size is about 133.5MiB, exceeding the 128MiB AS guard, while RSS
is about 28.4MiB. Failed-constructor allocations can already be freed when the
failure snapshot is taken. Those low post-failure samples do not measure the
failed allocation size. `/proc/self/status` VmSize/VmRSS are instantaneous;
`getrusage().ru_maxrss` is a separate kernel high-water counter including startup.
Probe CPU/wall times include snapshots and round-trip checking. These metrics
are retained independently, not reconciled into an invented peak. Virtual
peak and aggregate workload memory peak remain null.

The host liblzma 5.4.5 `lzma_easy_encoder_memusage()` estimates are 97,620,491
bytes for preset 6, 32,608,775 for 3, and 2,855,431 for 0. These are library
**estimates**, not RSS measurements. The [XZ manual](https://tukaani.org/xz/man/xz.1.html)
also documents preset 6's substantial encoder requirement. Library lookup and
these estimates happen in the probe driver, outside measured children.

Together, the empty-input failure, constructor-only phase isolation, virtual
size/RSS separation, lower-preset controls, and successful unchanged archive at
higher AS identify this reproduction as **encoder initialization hitting the
additional address-space guard**, not evidence of input-size exhaustion or a
128MiB cgroup OOM. Larger or less compressible real inputs may still require
more resident memory; this small repeated-byte matrix is not a scaling study.
The exact AS threshold is runtime/platform dependent.

### Four-lane correctness and observation remain separate

New complete matrices `2026-10-28-as128.jsonl`, `...-as192.jsonl`, and
`...-as256.jsonl` each contain six campaigns (pool 1/2/4, unaudited/audited,
one repetition): **18 campaigns / 72 attempts**, additional to the baseline.
These are exploratory runs on the same 8GiB, quota-8-CPU host; some diagnostic
launches overlap. Their timing fields must not establish scaling or overhead.

| Lane | Unaudited AS128 | Unaudited AS192 | Unaudited AS256 | Worker RSS high-water across complete matrices |
| --- | --- | --- | --- | ---: |
| gh_identity | 3/3 correct | 3/3 correct | 3/3 correct | 49,373,184–49,381,376 bytes |
| xprobe | 3/3 correct | 3/3 correct | 3/3 correct | 25,460,736–25,468,928 bytes |
| Ironmate | 3/3 correct | 3/3 correct | 3/3 correct | 19,791,872 bytes |
| mcp-toolcall-lab | 3 initialization failures | 3/3 correct | 3/3 correct | 31,850,496 failed; 32,112,640 successful |

Successful oracle result hashes are stable across these conditions. These are
per-worker maxima, **never summed into an aggregate peak**. The change that
resolves the archive's diagnostic failure is solely selecting AS192 or AS256;
it does not alter the existing XZ archive encoder or source pins.

All 36 audited attempts in the complete matrices fail with `tracing_error`
before a worker receipt. Worker correctness/RSS are unavailable for these
attempts, external totals remain null, and dependency classification is unknown.
No valid traced/untraced overhead pair exists.

An additional first AS256 launch retained only the pool-1 pair before stopping;
its eight attempts are preserved in `2026-10-28-as256-partial.jsonl`, outside the
complete-matrix counts above. The stopping reason was not captured (unknown).
Its unaudited four lanes passed; its four audited lanes have tracing errors.
It must not be presented as a complete campaign matrix.

The probe records visible cgroup limits/counters and an empty-subgroup creation
attempt: `memory.max=8589934592`, `cpu.max=800000 100000`, creation fails with
errno 30 (read-only filesystem). It never changes the enclosing cgroup. No
128MiB-cgroup/CPU1 run was possible. Shared cgroup counters and RSS from the
larger host do not predict acceptance; that result remains **unknown**.

### Reproduce and regress

```sh
python -S tools/constrained_lab_lzma_probe.py --output new-lzma-probes.jsonl
# Staged repos must still match the original manifest exactly:
python -S tools/constrained_lab_workloads.py --staged ../staged --exploratory --repeats 1 --address-space-mib 192 --output new-as192.jsonl
# In a dedicated 128MiB/CPU1 cgroup, omit --exploratory; AS192 is a separate guard:
python -S tools/constrained_lab_workloads.py --staged ../staged --address-space-mib 192 --output new-acceptance-attempt.jsonl
python -S -m unittest discover -s tests -p 'test_constrained_lab_workloads.py' -v
python -S -m unittest discover -s tests -p 'test_constrained_lab_lzma_probe.py' -v
```

The 11 focused tests cover larger-AS fail-closed budget verification, propagation
of the selected guard, failure receipts, phase attribution with null metrics,
evidence hashes/pins, empty-input/default-6 controls, stable independent-oracle
results, and unavailable-audit separation. Dedicated-cgroup aggregate memory,
CPU1 acceptance, permitted tracing and external dependency classification remain
open. Keep this PR **Draft**, and #143/#133 open; no acceptance or merge is claimed.

Additional validation: the unchanged auditor regression suite ran 19 tests,
with 18 passing and its live-strace CI-only check skipped. `git diff --check` passed.

## Environment qualification and acceptance route (2026-10-29)

**Status: blocked here; acceptance unknown.** Rechecked at head
b22f1a1a8780401d22da7feb6b3c1857ae9f9738. The current execution host exposes
cgroup v2 read-only, memory.max=8589934592, cpu.max="800000 100000",
memory.swap.max=max. Creating an empty subgroup fails with EROFS (errno 30).
Docker and Podman are absent. A separate
`strace -f -e trace=execve,execveat -o /tmp/probe.trace /usr/bin/true`
exits 1 with PTRACE_TRACEME / PTRACE_SETOPTIONS "Operation not permitted".
UID 0 therefore proves neither cgroup delegation nor ptrace permission.
No enclosing cgroup, mount, or security policy was modified.

### Available routes and qualification gates

| Route | Required evidence before running | Current status |
| --- | --- | --- |
| Rootful Docker on a Linux VM | cgroup v2 memory/CPU controllers, functioning memory+swap enforcement, private cgroup namespace, readable counters, successful child ptrace probe | Candidate; not executed here |
| Delegated cgroup v2 on Linux | Dedicated empty subtree, delegated memory/cpu controllers, limits configured before attaching any measured process, separate observer outside the subtree | Candidate; delegation unavailable here |
| GitHub-hosted Linux VM | Host job (not nested job container), Docker/cgroup/preflight checked at runtime; stage inputs before offline container | Candidate; no qualifying run recorded |
| Self-hosted Linux runner | Same gates, plus disposable dedicated execution group and no unrelated processes | Candidate; no runner availability established |

The existing constrained-lab.yml runs unittest smoke only. Its path filter omits
the new workload harness and fixtures; green smoke/ordinary Python CI does not
qualify a resource experiment. No resource acceptance job is currently provided.
GitHub documents passwordless sudo on Linux VMs, but that is a possible setup
route, not evidence that any particular job has passed cgroup/ptrace gates.
Use an explicit supported Ubuntu VM label (for example ubuntu-24.04), read-only
contents permission, no deployment permission, and an always-run evidence upload
when a dedicated acceptance workflow is added. Do not close #143/#133 from it.

### Docker reproduction recipe (candidate, not a successful run)

Prepare all four repositories with the existing manifest reproduction above.
Keep every SHA, fixture digest, compression setting and seed unchanged.
Before going offline, build an image containing Python, git (source validation),
strace and libseccomp.so.2; retain its Dockerfile, package versions and immutable
image ID/digest. Do not install packages or clone during the measurement.
The image must support the existing Python APIs without third-party packages.

Set REAL_WORKLOAD_IMAGE to that locally prepared immutable image ID/digest.
From the repository root, with ../staged already populated:

```sh
mkdir -p acceptance-evidence
docker info > acceptance-evidence/docker-info.txt
docker image inspect "$REAL_WORKLOAD_IMAGE" > acceptance-evidence/image.json
docker create --name pr175-acceptance --cgroupns=private \
  --memory=128m --memory-swap=128m --cpus=1 --network=none \
  --mount type=bind,src="$(pwd)",dst=/repo,readonly \
  --mount type=bind,src="$(cd ../staged && pwd)",dst=/staged,readonly \
  --mount type=bind,src="$(pwd)/acceptance-evidence",dst=/evidence \
  --workdir /repo "$REAL_WORKLOAD_IMAGE" sh -c '
    set -eu
    for f in memory.max memory.swap.max cpu.max memory.current memory.peak memory.events cpu.stat; do
      cat "/sys/fs/cgroup/$f" > "/evidence/pre-$f"
    done
    test "$(cat /sys/fs/cgroup/memory.max)" = 134217728
    test "$(cat /sys/fs/cgroup/memory.swap.max)" = 0
    python -S -c "from tools.constrained_lab_resources import verify_budget; print(verify_budget())" > /evidence/budget.txt
    strace -f -e trace=execve,execveat -o /evidence/ptrace.trace /usr/bin/true
    set +e
    python -S tools/constrained_lab_workloads.py --staged /staged \
      --address-space-mib 192 --repeats 3 --output /evidence/results.jsonl
    rc=$?
    for f in memory.current memory.peak memory.events cpu.stat; do
      cat "/sys/fs/cgroup/$f" > "/evidence/post-$f"
    done
    exit "$rc"
  '
docker inspect pr175-acceptance > acceptance-evidence/container-before.json
docker start --attach pr175-acceptance
# Run these even if attach/preflight/workload failed; preserve exit/OOM evidence:
docker inspect pr175-acceptance > acceptance-evidence/container-after.json
docker logs pr175-acceptance > acceptance-evidence/container.log 2>&1
```

First try the default container security profile. If the independent ptrace
probe fails, classify audit unavailable; investigate the actual host LSM,
seccomp and ptrace restrictions. A reviewed SYS_PTRACE capability/profile
adjustment may be needed on a dedicated VM; neither adding the capability nor
running as root guarantees success. Do not use --privileged, disable all
seccomp, or expose a host Docker socket inside the measured container.
Run preflight and measurements with identical security settings.
Keep source mounts read-only and place Python bytecode outside staged sources
(or disable bytecode) so repeated validation still sees clean worktrees.

AS192 above is only the additional per-process virtual-address guard. The
physical/accounted group budget remains 128MiB with swap disabled. It avoids
the already identified AS128 initialization confound without changing XZ.
This does not predict success: concurrent workers, the Python coordinator,
auditor, page cache and kernel charges may exhaust the shared 128MiB budget.
CPU1 means quota/period <= 1, not a single affinity CPU or exclusive physical
core. Four separate 128MiB containers would instead allow 512MiB aggregate
and do not demonstrate this shared-budget reference.

### Evidence interpretation and remaining instrumentation

A fresh container isolates the whole attempt's cgroup high-water from prior
runs, but its memory.peak includes preflight and all campaigns. Retain it as
**container-attempt peak including setup**, never as an individual campaign
peak. read_cgroup/verify_budget currently read /sys/fs/cgroup directly; this
works only when that path represents the executing group's private namespace.
For a host delegated subtree, resolve /proc/self/cgroup and mountinfo or
provide an explicitly verified group path; simply creating a host subgroup
does not make the existing hard-coded reader target it.

For attributable campaign peaks, launch one campaign in each fresh dedicated
group (requires a harness selector/outer controller), or use a verified
kernel-supported peak-reset mechanism. Do not subtract peaks or sum per-worker
ru_maxrss. An external observer must retain final memory.peak, memory.events,
cpu.stat and exit/OOM state if the coordinator itself is killed. Do not delete
the container/group before collecting evidence. A missing post sample is unknown.
The recipe above does not implement per-campaign peak attribution or separate
per-worker limits under a host parent budget, as required by the broader #133.

Acceptance additionally requires:
- all expected worker receipts, independent oracle hashes, unchanged provenance,
  and complete matrices; exit zero alone is insufficient;
- evidence that the four pool-4 tasks actually overlap (start/end intervals or
  synchronization instrumentation); max_workers=4 alone is not proof;
- valid auditor completion without truncation/timeouts/parser loss; a successful
  ptrace smoke probe alone does not establish complete executable observation;
- matched successful audited/unaudited pairs before any overhead conclusion.

Until those measurements exist, retain four-lane concurrency, attributable
aggregate peak, complete dependencies and tracing overhead as null/unknown.
A cgroup OOM is a measured failure of this allocation, not an unavailable
environment and not proof that a larger AS cap fixes physical-memory demand.
Keep PR #175 Draft and Issues #143/#133 open.

Primary references:
- https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
- https://docs.docker.com/engine/containers/resource_constraints/
- https://docs.docker.com/engine/security/seccomp/
- https://docs.github.com/en/actions/reference/runners/github-hosted-runners
