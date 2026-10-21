# Constrained lab: seeded campaigns and comparative benchmark

Tracks #133. The runner assigns each trusted case to exactly one deterministic seeded shard.
Use `python -S tools/constrained_lab.py cases.json --rounds 10 --seed 7 --workers 2`.
A round stops on failure by default, or continues with `--keep-going`.
Empty shards are not independent evidence. A pass fraction is coverage, not a success probability.

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
The runner is NOT a sandbox: network isolation, memory/CPU quotas, peak RSS,
child CPU accounting, energy measurement and bounded streaming output remain future work.
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
