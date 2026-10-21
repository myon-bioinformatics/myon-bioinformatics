# Constrained lab: first GHA smoke slice

Tracks #133. Each trusted case is assigned to exactly one deterministic seeded shard.
Use `python -S tools/constrained_lab.py cases.json --rounds 10 --seed 7 --workers 2`.
A round fails fast by default, or continues with `--keep-going`.
Empty shards are not independent evidence. A pass fraction is coverage, **not** an estimated probability.

The GHA workflow only runs trusted self-tests and has a five-minute job timeout.
It does not run arbitrary user-supplied commands or benchmark the four OSS repos yet.
This runner is NOT a sandbox: OS-level network isolation, memory and CPU quotas,
peak RSS, child CPU accounting, energy measurement and hard output limits remain future work.
