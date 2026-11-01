# Dependency-only CI preparation

`tools/nano_node.py --dependencies-only` prepares a private npm lock projection
from exactly two pinned inputs: `package.json` and `package-lock.json`. It does
not install packages, select tests, copy application/test source, or validate the
checkout commit. Unrelated live source edits are intentionally allowed; changed
package inputs fail closed and require a reviewed pin update. Without this flag,
the existing Ironmate/BTK full-snapshot behavior remains unchanged.

**Upgrade tradeoff:** any package manifest/lock change, including a Dependabot
update to an unused package, fails the exact-input check until the parent
selector pins are reviewed and updated and the child pins that merged parent.
The error reports expected and actual Git blobs. This does not forbid upgrades
or change other open PRs; it requires coordinated review rather than silently
running an old lock. Do not disable the guard to make a dependency PR green.

Two initial CI consumers are planned:

| Named feature | Source directory | Original → selected lock records | Live test boundary |
| --- | --- | --- | --- |
| `browser-test-kit-document-mirrors` | BTK repository root | 221 → 30 | Existing `playwright.yml:node`, all five browser projects. Keep the full ExcelJS/XLSX browserless job unchanged. |
| `markdown-toml` | Markdown `tests/mirror` | 32 → 1 (`smol-toml`) | Existing `ci.yml:node-runtime`, all six OS/Node rows and 15 TOML tests per row. |

Markdown's new selector requires the explicit dependency-only flag. It does not
stand in for the full independent Markdown/HTML/string-width reader suite.
Counts are locked package records, not a new installation-size measurement.

The CLI returns a private directory with `receipt.json`, original `inputs/`,
and the projected project (`browser-test-kit/` or `markdown-toml/`). The receipt
records the exact input blobs, projected SHA-256 values and limited verification
scope. The official npm offline projection must preserve every retained record,
including versions, integrity hashes and reviewed optional-peer metadata.

Consumers must pin a **merged** parent commit and verify the helper's SHA-256
before execution. They install only the projected lock in private job scratch,
check the selected packages are present, then expose only `node_modules` to the
**live checkout**. Test commands, configuration, matrices, existing artifacts,
browser engines and job dependencies remain owned by the live child workflow.
Never run historical staged tests in place of current tests. In Markdown, the
existing test fixture specifically requires `tests/mirror/node_modules/smol-toml`;
a missing package must fail setup rather than turn the tests into skips.

There is no new job, cross-job node_modules artifact, shared mutable environment,
permission expansion, dependency upgrade or default runtime dependency change.
This is package preparation, not process/container isolation.

## Timing limits

The previous hosted BTK Node job's full npm install took about 2 seconds, versus
28 seconds for browser OS dependencies, 10 seconds for engine download and
44 seconds for tests (job 124728157391, run 41265671916). Markdown's six previous
Node rows installed the full reader set in 0–5 seconds (run 41277225993).
Helper download/projection/copy overhead may offset installation savings. Compare
whole setup steps and existing job durations after CI; do not infer a speedup or
parallelism improvement from package counts. No extra benchmark job is added.

Historical measured bundles retain their original helper/source hashes. The
[BTK reproduction recipe](evidence/nano-features/2026-11-01-browser-test-kit/REPRODUCE.md)
pins its historical parent checkout; these CI changes do not relabel that run.
