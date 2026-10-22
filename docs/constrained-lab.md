# Constrained lab: seeded campaigns and comparative benchmark

Tracks #133. The runner assigns each trusted case to exactly one deterministic seeded shard.
The shared [small-runtime-first principle](small-runtime-principles.md) explains
why we start with Python/Node commands and reusable stdlib/built-in helpers,
and how relevant PRs should report capability, quality and resource tradeoffs.
The aim is to measure what a small environment can do, not to require a hardened
sandbox or a full benchmark suite for every change. Isolation requirements below
apply when making the corresponding offline/resource-bound claims.
For repeatable post-merge ancestry and test verification in a disposable checkout,
see [verify checkout](verify-checkout.md).
Use `python -S tools/constrained_lab.py cases.json --rounds 10 --seed 7 --workers 2`.
A round stops on failure by default, or continues with `--keep-going`.
Empty shards are not independent evidence. A pass fraction is coverage, not a success probability.
The case list must be nonempty and `--rounds` must not exceed the case count.
Both the runner and comparison CLI reject these invalid inputs with exit 2 before
executing any case or emitting a success report. For fewer than 10 cases, set the
runner's `--rounds` explicitly (for example, `--rounds 1` for a single case).

## Compare sequential and concurrent runs

```sh
python -S tools/constrained_lab_bench.py cases.json --rounds 1 --workers 2 --seed 7
```

Both runs use the same seed and case IDs. Compare result identities and output hashes,
then report measured wall-clock speedup. No minimum speedup is asserted in CI because
host contention is variable. **With one case per shard there is no intra-round
parallelism**; use multiple independent cases per round when benchmarking concurrency.
This comparison executes each case twice (once per configuration), whereas an individual
campaign executes each case once.

The GHA smoke runs only trusted self-tests under a five-minute job timeout.
The runner is NOT a sandbox: network isolation, memory/CPU quotas, complete
process-tree accounting and energy measurement remain future work. Bounded output
and limited worker resource measurements are described below.
Do not run untrusted commands or assume the benchmark is an energy measurement.


## Resource measurement (Linux cgroup v2)

```sh
python -S tools/constrained_lab_resources.py --timeout 10 python -S -c 'print(1)'
```

This is an **opt-in** cgroup-v2 measurement command; it reports the whole cgroup's
CPU usage delta, throttled CPU time, current memory, historic memory peak and memory
limit. It does **not** enforce quotas or block network access. Use an isolated
cgroup/container with `memory.max=134217728`, a separately configured CPU quota,
and network isolation before claiming a 128 MiB offline run.

Important: `memory.peak` is the historical cgroup high-water mark, not necessarily
the peak attributable to this invocation. It can include other processes and prior
work. Likewise CPU usage includes every process in the cgroup. Run one workload
per fresh isolated cgroup for meaningful measurements. These counters are not
an energy or per-worker measurement. Non-Linux systems need a separate adapter.

Future integration: wire immutable Ironmate pixel-grid fixtures and NagoyAction
seeded shard fixtures into trusted case manifests, then compare sequential and
concurrent runs under a **shared** cgroup budget. Avoid asserting speedup in CI.

## Opt-in fail-closed budget preflight

```sh
python -S tools/constrained_lab_resources.py --verify-budget \
  --max-memory-bytes 134217728 --max-cpu-fraction 1 \
  python -S -c 'print(1)'
```

This checks the current cgroup's `memory.max` and `cpu.max` **before**
launching the command, rejecting unbounded or weaker limits. The OS must still
configure/enforce those limits. This does not prove network isolation,
per-process memory peaks, or child-process containment.

## Algorithm microbenchmarks (representative fixtures only)

```sh
python -S tools/constrained_lab_algorithms.py --seed 7 --width 1024 --trials 5
```

Compares set intersection against a pure-Python integer-bitset implementation on
identical seeded inputs and checks output hashes. Also tests repeated tile
deduplication and malformed/partial images. This does **not** yet execute
Ironmate's own image algorithms or NagoyAction's own shard planner; do not
claim repository-level performance gains from these synthetic fixtures.
Precomputation, conversion and memory costs are not yet fully measured.

## Staged offline OSS fixture checks

`python -S tools/constrained_lab_fixtures.py manifest.json --root /staged/snapshot`

The manifest names trusted local commands and their expected exit codes and stdout
SHA-256. Empty manifests are rejected with exit 2 before execution; zero checks
cannot establish success. A mismatch is reported as a failure. This enables checking real
Ironmate/NagoyAction/GHI/xprobe source snapshots **after** they have been
staged and pinned, without a runtime GitHub API call. The fixture runner does
not fetch or build those projects; none of their actual workloads have yet
been benchmarked by this PR. The caller must enforce offline isolation,
memory/CPU quotas and trusted commands. Do not feed untrusted manifests.

### Source provenance for staged workloads

Fixture entries may include an optional `sources` object mapping relative source
paths to SHA-256 digests. The runner checks each file before launching the command,
rejects parent-directory traversal and symlink targets, and fails closed on changed
source bytes. This protects reproducibility of the checked files only; it is not
an execution sandbox, nor a guarantee that all transitive imports are pinned.
For real Ironmate/NagoyAction runs, stage snapshots outside the CI job's
runtime network path and enumerate the executable entry point plus dependencies.

## Optional staged real-code contract probe

Stage immutable NagoyAction `localci.py` or Ironmate `browser_matrix.py`
(and Ironmate's `image_compare.py` and `photo_grid.py`) **before**
the offline run. Record and supply SHA-256 digests:

```sh
python -S tools/constrained_lab_oss.py nagoyaction /staged/NagoyAction/localci.py --sha256 DIGEST
python -S tools/constrained_lab_oss.py ironmate /staged/Ironmate/browser_matrix.py \
  --sha256 DIGEST --dependency /staged/Ironmate/image_compare.py:DIGEST \
  --dependency /staged/Ironmate/photo_grid.py:DIGEST
```

The actual source module is imported after SHA verification. The tests bundled
in this PR use small contract doubles and are **not evidence of an actual
Ironmate or NagoyAction performance result**. Python tracemalloc tracks Python
allocations, not RSS or whole-cgroup memory. Actual OSS measurements require
staged files, pinned transitive imports, offline OS isolation, and a fresh
cgroup with enforced budgets. No live source is fetched by this probe.

## Staged two-repository sequential/parallel integration

```sh
python -S tools/constrained_lab_integrate.py --root /staged \
  --nagoyaction /staged/NagoyAction/localci.py \
  --ironmate /staged/Ironmate/browser_matrix.py --rounds 1 --workers 2
```

Runs two staged source entry points with SHA-pinned imports, compares the same
cases with one and two workers, and checks status/output-hash equivalence.
The integration opts into `stdout_contract: "result-measurements/1"` and the
probe's `--structured-output`: stdout contains a `result` object and a separate
`measurements` object. Only the canonical JSON hash of `result` is compared,
alongside case ID, status, exit code and stderr hash. All deterministic fields
(including pass, case/shard counts and PNG digest/size) remain in `result`.
Wall time and Python allocation peak remain in each case's `measurements`;
the raw stdout hash is also retained for audit. Malformed envelopes fail the
case. Commands without this explicit contract retain whole-stdout comparison,
and the probe's default flat JSON output is unchanged.
The bundled CI tests use minimal fixture modules; they are **not** real OSS
performance results. Stage Ironmate's import dependencies alongside
`browser_matrix.py`. This command does not enforce cgroup limits or
network isolation, nor does it measure complete process-tree RSS or total CPU time.
For an actual 128 MiB offline claim, execute inside a fresh isolated
cgroup with quotas and record the resource probe separately.

## Bounded output and worker resource accounting

The runner and benchmark accept `--max-output-bytes N` (default **1 MiB per
stream**) in addition to their existing CLI flags. Each worker streams stdout
and stderr independently into bounded byte buffers. Exceeding either limit is
a non-success `output_limit` status; the POSIX process group is killed (or
the direct process on Windows). Timeouts apply until both the worker and pipe
draining finish, including workers that close stdout/stderr before exiting.
Draining is cancellable: descendants holding inherited pipes cannot delay the
timeout or output-limit result, even if they leave the original process group.
Such escaped descendants are **not** terminated by the runner.
Only complete output from non-timeout, non-output-limit runs receives SHA-256 digests or
structured output validation. Jobs are trusted commands, **not a sandbox**.

On platforms supporting POSIX `wait4`, worker user/system CPU seconds are
measured at reaping. These OS counters **may include descendants that the worker
waited for**; they are neither direct-process-only nor complete process-tree
measurements. Linux and macOS also expose `wait4` peak RSS (Linux `ru_maxrss` KiB,
macOS bytes); it can include a waited-for descendant's peak rather than the
worker's own peak, and is not a sum of descendant peaks. The source label is
`wait4/worker-with-reaped-descendants`. Where unavailable, report JSON `null`, not
a tracemalloc or cgroup substitute. The `resources` summary contains sums of
measured worker CPU seconds and individual `wait4` peak RSS values, with coverage counts.
**The sum of individual RSS peaks is not the simultaneous peak.** Linux additionally
samples concurrent current RSS of live direct children via `/proc` every 10 ms;
short-lived peaks can be missed, shared pages can be double-counted, and neither
descendant processes nor the runner's own memory are included. The
`aggregate_host_peak_rss_bytes` field intentionally remains `null`.
The benchmark preserves strict output/result identity while reporting both
configurations' wall time and resource measurements. No deterministic speedup
is required in CI.

Outstanding: cgroup quota enforcement, complete descendant accounting, total
host high-water mark, Windows process-tree termination/CPU-RSS adapter, network
isolation, complete sandboxing and the four-repository comparative experiment.
