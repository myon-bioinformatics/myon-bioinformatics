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
