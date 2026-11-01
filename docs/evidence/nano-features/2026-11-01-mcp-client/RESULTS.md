# MCP caller-only environment: bounded observations

Exact child source: `06cdc8e4e14d50de45c8b8122a5ccff6911c3404`.
Real unchanged `chat_sim.send_direct`/`send_via_chat`, MCP2.3.0/Pydantic2.14.1,
against the same unchanged FastMCP4.1.3 local fixture. No model, external API,
browser or Docker was used. This is one selected feature, not full-repository
or cross-repository completion.

| Observation | Full default runtime caller | Pruned SDK caller |
|---|---:|---:|
| Export requirement rows, including markers |72|30|
| Actual Linux installed distributions |69|28|
| Venv apparent regular-file bytes |58,981,931|30,223,954|
| Venv allocated regular-file bytes |67,227,648|32,616,448|
| Median install seconds,3 completed trials |9.848|7.994|
| Median2-job batch seconds,1 worker |3.148|3.106|
| Median2-job batch seconds,2 workers |1.999|1.911|

The caller environment is smaller, but the full FastMCP fixture is still
necessary. A thin caller plus a separate fixture environment totals
**89,205,885 apparent bytes**, versus **58,981,931** for one shared full
environment. This is **not whole-host or whole-service savings**. Python/stdlib,
symlink targets, source and retained caches are outside venv byte counts.

All6 fresh-cache binary/hash-locked official-PyPI installs succeeded in balanced
AB/BA/AB order. No version upgrades, retries, builds or alternate endpoints.
Requirements/harness/source identities were verified before every install and
after completion. Workloads explicitly selected complete matched trial3.

## Correctness and process accounting

- 8 synthetic success/empty/error cases through both real client functions.
- 12 batches,24 client workers,384 real local server calls validated.
- Independent exact mock expectations passed, including fictional Exampleville,
  code90001, JR station, price52,000,000/year2025, empty-success and error flags.
- Every complete SDK JSON/chat output matched; canonical SHA-256:
  `393b226a0c82866e82f6395dcf2cab8dcc96f1211dc9e351d348aa6ff865f4af`.
- 24 clients exited0. Each fixture server was intentionally SIGTERM-terminated
  and reaped after both jobs completed:12 exits of-15. These are planned shutdowns,
  not unexpected deaths. An ad-hoc terminal checker initially required exit0;
  its expectation was corrected without rerunning or changing any measurement.
- Client audit denials0; no blocked-operation error appeared in fixture logs.
  Only fixed local mock calls were sent; live catalog tools were not invoked.
  Server denied-attempt count is unknown: its in-memory counter was not persisted
  and planned SIGTERM may bypass the final check. No-errors-in-log is not count0.
- 21 stdlib/fake preparation tests and10 unchanged catalog unittest cases passed.
  Original integration tests and the full child suite were not run here, altered
  or deleted. Their baseline/test-extra import requirements remain explicit.

## Limits

Three small trials are observations, not a general installation/runtime speed
claim. Both caller profiles use identical algorithms and the same server.
Unused installed packages need not materially change runtime. Each batch has
exactly2 jobs, so these results do not identify an optimal worker count.

RSS is sampled every20ms, with separate caller, fixture and simultaneous combined
figures in `evidence.json`. Shared pages can be double-counted and peaks missed;
this is not PSS. Worker high-water is read after calls, before receipt packaging.
The pre-run host exposed9 CPUs/affinity entries, but effective CPU/memory quotas
were unavailable; no exclusive hardware allocation is claimed. OS/DNS/CDN caches
were not flushed. Retained cache bytes are not network transfer bytes.

`evidence.json` preserves all6 install and12 batch numeric samples,24 per-worker
hashes/statuses, exact pins, raw-artifact hashes and unknowns. The complete output
appears once in `output-reference.json`. Source staging and commands are in
`REPRODUCE.md`; original raw logs/receipts/harness remain separate for audit.
