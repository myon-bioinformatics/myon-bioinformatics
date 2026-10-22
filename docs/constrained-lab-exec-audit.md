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
A traced shell invocation is still an external executable even if the
command subsequently uses a shell builtin. Distinguish native Python/Node
APIs from actual child process launches.

This first slice is *observation scaffolding*, not the four-lane comparison:
it does not yet measure host budgets, capture source/fixture provenance,
prove complete descendant coverage, provide an audit backend without strace,
or assert that any workload is Python/Node-only. strace overhead must be
measured separately; kernel permissions and detached children may cause gaps.
Do not call this a sandbox, a security boundary, or an enforced allowlist.
