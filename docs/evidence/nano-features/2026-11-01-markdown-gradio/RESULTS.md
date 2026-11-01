# markdown Gradio: validated text API, incomplete install repetitions

The unchanged Gradio app at child revision
`f04d233d2edc63650439fbb7692434e9f2c66266` produced identical complete callback
outputs in both environments. Real `build_app`/queue/HTTP-SSE/gradio-client
routing ran—not an analyze-only substitute. Scope is **text inputs with no
upload**; browser/widget rendering, uploads and the full child suite are untested.

| Observation | Full frontend group | Gradio-only selection |
|---|---:|---:|
| Locked export rows,including markers |77|56|
| Actual Linux installed distributions |72|52|
| Venv apparent regular-file bytes |642,781,043|239,130,663|
| Venv allocated regular-file bytes |667,291,648|257,626,112|
| Single successful install observation,seconds |17.575|10.445|
| Median2-job batch seconds,1 worker,3 runtime repeats |10.270|9.901|
| Median2-job batch seconds,2 workers,3 runtime repeats |5.556|5.074|

Both exports exclude default dev groups and the project; only Streamlit and
Playwright are pruned from the feature selection. Real Gradio6.30.0,
gradio-client2.7.2,NumPy,Pandas,Pillow and their fixed closure remain. This is not
a stdlib replacement. Every job includes its own real local app/client,so there
is no separately required server environment omitted from the runtime comparison.

## Preserve the installation failure

The plan was3 paired installs in AB/BA/AB order. **Only the first pair completed.**
Trial2 Gradio failed exit2 after13.504seconds when the official NumPy2.5.4 wheel
connection timed out. The runner stopped:trial2 full and both trial3 installs
never started. No retry,alternate host/proxy,escalation or version change.

Therefore installation timing has **n=1 per profile**,not3,and no install median
or general speed claim is made. The partial failed venv/cache are retained and
excluded from successful footprint numbers. Local API work explicitly selected
the already successful matchedtrial1; its3 runtime repetitions do not create
additional install samples. Overall installation-plan completion remains false.

## Correctness and safety evidence

- 12 runtime batches,24 API workers,144 successful complete API responses.
-3 synthetic fixtures through both raw HTTP/SSE and Gradio client routes.
  All four returned fields,including the complete JSON summary string,matched
  independent fixed expectations and both environments.
- Canonical output SHA-256:
  `42da35e1717439d3ca5328356031f78efa9d2a8bee5f209000aec7993da417b7`.
  Opaque event/session IDs are retained in raw artifacts,not confused with
  callback-content parity. URL text in fixtures was never fetched.
- All4 unchanged original API tests passed in each profile:8 test executions,
  zero skips,8 narrowly allowed original curl subprocess calls. Browser fixture
  and broader frontend tests remained untouched and were not invoked.
- All26 worker processes exited0 and were reaped.952 persisted socket/process
  audit events contained0 denied attempts. Runtime connections stayed within
  registered IPv4 loopback ports; analytics/HF telemetry were disabled before
  imports. No model,external API,browser or Docker was used.
- Source,requirements,test-tool and harness identities verified after runtime.
  25 preparation fake/stdlib tests passed;2 reproduction-staging fake tests also
  passed afterward. These are separate from original app acceptance tests.

## Accounting and limits

Python3.12.14/stdlib and symlink targets are outside venv bytes. Retained cache
bytes are not compressed network transfer. Runtime source is2 unchanged files,
276,418bytes. Separately reused pytest/iniconfig/pluggy test tools are121 files,
1,454,604bytes,and host curl8.14.1 is separate; neither inflates measured runtime
venv counts. Every installer and worker has private HOME/config/tmp/cache state.

All batch and RSS samples appear in `evidence.json`. Sum-RSS is sampled20ms,
may miss peaks/double-count shared pages,and is not PSS. App startup/shutdown and
process overhead are included. The few fixed2-job trials neither establish a
general speed benefit nor identify an optimal worker count. OS/DNS/CDN caches
were not flushed; effective CPU/memory quotas remain unknown despite visible
host CPU/affinity/MemTotal observations.

Raw logs/receipts remain unchanged locally. The compact bundle records every
attempt,unstarted trial,per-batch/per-worker numeric result and raw-artifact hash;
complete callback output appears once in `output-reference.json`. Follow
`REPRODUCE.md` for exact source/test-tool staging and required uv-placeholder
configuration. No child adoption or merge was performed.
