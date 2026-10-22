# Small-runtime first: measure what ingenuity can do

The working hypothesis is that many useful tasks can be solved in a small
environment with **Python and/or Node.js commands**, standard libraries, and
reusable local source. Start there and measure how far careful algorithms and
composition can take us before adding a larger runtime or dependency stack.
This is an experiment in capability, correctness and efficiency, not primarily
a project to build a hardened security sandbox.

This principle guides the parent, reusable child tools and cross-cutting Flutter
work. It does not require replacing Flutter/Dart, browsers, native libraries or
other necessary project runtimes. For example, inspecting Flutter source or
summarizing test evidence with Python is a different claim from building or
running the Flutter application with Python alone.

## Prefer a small, reusable solution

- Establish a simple, correct reference implementation and a representative
  input set before optimizing. Use existing stdlib or Node built-in facilities
  and reusable project helpers where they fit.
- A one-liner is a useful entry point, not a code-golf goal. Keep its inputs,
  outputs, errors and prerequisites explicit; move repeated or nontrivial logic
  into a readable, tested helper rather than duplicating opaque commands.
- Try reducing work before adding resources: select only relevant inputs,
  stream large data, reuse verified intermediates, or choose bitsets, sparse
  representations and suitable algorithms. Measure setup, conversion,
  serialization and cache costs too, not only the attractive inner loop.
- Add a dependency or richer runtime when its benefits justify it. Record the
  specific capability or measured tradeoff that the small baseline cannot meet;
  do not describe an untested alternative as impossible.

## Compare results, not just speed

Use the same task, input identities, seeds and output contract for the reference
and candidate. Separate deterministic results from variable measurements.
For an exact optimization, compare complete outputs or canonical hashes and
check failures as well as successful cases. For an approximation, explicitly
state the changed contract, quality metric, acceptable error and any omitted
work. Partial coverage or lower fidelity is not equivalent correctness.

Measure correctness, quality/completeness, wall time and memory together when
claiming an improvement. Include CPU time, startup cost, throughput, cache state
and repeated-run variation where relevant. Report regressions and unsuccessful
experiments too. Label unavailable measurements as **not measured**, rather than
using an unrelated counter as a substitute: Python allocation peaks are not
process RSS; whole-cgroup history is not a per-worker invocation peak.

The small-runtime baseline concerns available tools and dependencies. A
resource-constrained comparison is a separate dimension: record the actual
environment and any configured limits. Neither 128 MiB nor one CPU is a fixed
entitlement or universal success target. If workers run concurrently, compare
sequential and concurrent execution on the same corpus and account for both
per-worker use and aggregate host use, including orchestration and buffers.
Do not claim total memory savings from a single worker's measurement.

Exercise relevant malformed/empty inputs, missing prerequisites, stale or corrupt
cache entries, timeouts and interrupted work. Test resource exhaustion when a
resource-limit claim is in scope and the environment supports it. Distinguish
expected injected failures, unsupported capabilities, skipped work and unexpected
test failures. A failure with a useful reproduction is evidence, not a reason
to silently narrow the reported workload.

## Proportionate evidence for a relevant PR

Use this checklist for changes that claim smaller runtime requirements,
algorithmic efficiency, approximation quality or parallel throughput. A small
local comparison can be sufficient. Documentation-only and unrelated PRs do
not need a new benchmark, and this guide does not add CI gates or require every
PR to provision an isolated resource lab.

1. **Question and scope:** What useful task should work in the small environment?
   Name the Python/Node version, required commands, dependencies and any larger
   runtime still needed. State what is not being tested.
2. **Reproduction:** Give source revision/digests, input IDs or fixtures, seeds,
   commands and relevant host/limit details. Link the retained raw results.
3. **Reference versus candidate:** State the baseline, optimization or
   approximation, output contract, correctness/quality result and skipped work.
   Include relevant failure cases and their expected outcomes.
4. **Cost and tradeoffs:** Report measured time and memory with their scope and
   units; include preparation/cache costs and variation where material. For
   parallel work, include worker count and per-worker/aggregate evidence, or
   explicitly mark missing accounting. Do not claim unmeasured gains.
5. **Decision and limits:** Explain what to reuse, what did not improve and what
   remains unknown. Record independent review and applicable current-head CI;
   a skipped or untriggered job is not a pass.

The [PR template](../.github/PULL_REQUEST_TEMPLATE.md) links here rather than
imposing this full checklist on every change. The parent keeps the common
principle; child and Flutter PRs can link to it and supply task-specific evidence
without duplicating the infrastructure or changing vendor adoption contracts.

## What the current lab establishes

At the merged [#135](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/135)
slice (`cc21a0a246fba54db36df7b4f074cb1649dabf0b`), the
[constrained-lab guide](constrained-lab.md) documents:

- Deterministic seeded trusted-case campaigns, timeout/error handling and
  sequential/concurrent result-identity comparison with separate timings.
- Synthetic set/bitset comparisons and tile fixtures. These are algorithm
  examples, not measurements of an entire downstream repository.
- Staged-source digest checks and NagoyAction/Ironmate contract-probe entry
  points. Bundled fixture doubles validate the harness, not real-project speed,
  complete PNG correctness or every transitive dependency.
- Opt-in existing-cgroup counters and budget preflight, not quota enforcement,
  network containment, per-worker RSS or aggregate workload accounting.

There is not yet a completed comparative report proving how far Python/Node-only
execution covers the four #133 lanes, or a measured best worker count/resource
allocation. A Node-only comparison is not established by Python harness tests.
The remaining work and earlier limitations stay tracked in
[#133](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/133).
This guide adds the shared method; it does not declare those criteria complete.

## Boundaries

Use trusted, staged workloads with the lab's documented prerequisites. The
helpers do not provide hard isolation. Ordinary small-runtime experiments do
not justify claims of enforced CPU/memory/network limits; those claims require
separate OS-level configuration and evidence. No security or quota settings are
changed by adopting this principle.

Python/Node availability is not a replacement for a GPU, a model runtime, or
hardware acceleration. A mock adapter, cache replay or synthetic benchmark does
not demonstrate real model inference, equivalent quality, energy savings or
whole-project performance. State the boundary and measure the actual workload
before making those claims.
