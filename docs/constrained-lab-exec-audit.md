# Opt-in executable dependency audit (Issue #143)

The hypothesis is **Python standard library + Node.js built-ins first**, not
that Git, grep, curl, shell or other executables are universally prohibited.
For every observed external executable, record its callsite, why it is needed,
and whether a Python/Node-native equivalent preserves correctness, cost,
portability and maintainability. Git is justified when real Git semantics
outweigh an ad hoc reimplementation; it is not justified automatically.

Use on **trusted, staged commands only**:

```sh
python -S tools/constrained_lab_exec_audit.py -- python -S -c 'import pathlib; print(len(list(pathlib.Path(".").iterdir())))'
python -S tools/constrained_lab_exec_audit.py -- node -e 'console.log(require("fs").readdirSync(".").length)'
```

This opt-in observation adapter uses an **external Linux strace installation**
to trace execve/execveat of the launched process tree. It does not add a
runtime dependency to the core constrained-lab runner. It intentionally
omits potentially sensitive argv/environment and returns `unknown` when
tracing is unavailable, partial or truncated; no observation is not evidence
that no external command ran. Bounded trace retention defaults to 1 MiB.
Raw trace is read through a private
Linux `/proc/self/fd` pipe, not spooled to disk. Exceeding the retained byte cap
returns `trace_limit`; deadline or cap violations trigger best-effort process-group
termination. Escaped descendants can survive, but cannot hold the reader open
past the deadline. This is output-retention control, not a host resource budget.
The JSON `observed_external_count` counts retained external exec attempts,
including failed attempts. `external_count` remains `null`: even a successful
tracer exit does not establish a complete workload total. Coverage is always
`partial-or-unknown` when events are present and `unknown` when none are present.
A missing/denied tracer or empty trace is never reported as a clean workload.
At most 1,000 events are returned; `events_truncated` signals an omitted event.
`execveat` with a named path is classified by that path; descriptor-only calls,
unfinished lines and unsupported formats remain unknown. Escaped or truncated
path strings are retained as strace displayed them but classified as unknown. Raw argv/environment
are not returned in the report, though the bounded transient trace buffer can
contain them while parsing. Workload stdout and stderr are discarded separately.

A traced shell invocation is still an external executable even if the
command subsequently uses a shell builtin. Distinguish native Python/Node
APIs from actual child process launches.

This first slice is *observation scaffolding*, not the four-lane comparison:
it does not yet measure host budgets, capture source/fixture provenance,
prove complete descendant coverage, provide an audit backend without strace,
or assert that any workload is Python/Node-only. strace overhead must be
measured separately; kernel permissions and detached children may cause gaps.
Do not call this a sandbox, a security boundary, or an enforced allowlist.

The pinned follow-up harness and its explicitly incomplete exploratory evidence
are documented in [Four real workloads](constrained-lab-real-workloads.md).
That experiment does not change this adapter's partial/unknown coverage contract
or establish completion of the four-lane resource demonstration.
