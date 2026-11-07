# In-process TestLoader selection experiment (2026-11-05)

Decision: retain this repository-local example and regressions; do not roll out
an additional runner, cache, CI job, or replacement for existing pytest commands.
Selection works, but a runtime improvement has not been established.
This applies the standard-API recipe validated in
[search_seq_including_spaces #63](https://github.com/myon-bioinformatics/search_seq_including_spaces/pull/63)
to `RuntimePlanTests` (27 existing tests).

## Executable minimum

Run from the repository root, using Python 3.11+:

```sh
python -S - <<'PYTHON'
import sys
import unittest
from unittest.mock import patch

with patch.object(sys, "path", ["tests", *sys.path]):
    loader = unittest.TestLoader()
    loader.testNamePatterns = ["*.test_examples_match_only_declared_requirements"]
    suite = loader.discover("tests", pattern="test_runtime_plan.py")
    if loader.errors:
        raise SystemExit("\n".join(loader.errors))
    if suite.countTestCases() == 0:
        raise SystemExit("No tests selected")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
PYTHON
```

For all cases, omit `testNamePatterns`. Multiple patterns are OR conditions on
full test IDs, using globs; this differs from CLI `-k` substring matching.
For exact selection replace discovery with
`loader.loadTestsFromNames(["test_runtime_plan.RuntimePlanTests.test_examples_match_only_declared_requirements"])`.
To load the whole module by name use `loadTestsFromNames(["test_runtime_plan"])`.
No directory walking, name parsing, `tests/__init__.py`, or common runner is
needed. The example restores `sys.path`. Imports still have normal Python
`sys.modules` caching: this is not a hot-reload mechanism.

Use a fresh loader, suite and result for every iteration. A consumed suite must
not be reused. No AST/source cache is introduced; source edits should be checked
in a fresh process rather than assumed visible in an already imported module.
A fresh loader also prevents a previous `loader.errors` list from contaminating
the next request. A nonexistent explicit name produces a loader error and a
failed test; a nonmatching pattern produces zero tests with no loader error.
`TestResult.wasSuccessful()` alone accepts zero tests, hence the explicit guard.

## Reproduce and inspect evidence

```sh
python -S -m unittest discover -s tests -p test_testloader_selection.py -v
python -m pytest tests/test_runtime_plan.py tests/test_testloader_selection.py -q
python experiments/testloader_selection.py > experiments/testloader-selection-results.json
```

The experiment requires pytest only for the comparison. The recipe and five
regression tests use the standard library. Existing pytest CI already collects
the regression file; no workflow changes or extra passes are needed.
The experiment is opt-in locally, not a new test execution interface.

The JSON records the baseline SHA, interpreter/platform, logical IDs, all raw
load/total timings, CLI logs, pytest outcomes, and invalid-name exits.
All, single and multiple selections compare exact logical ID sets and successful
results across `discover`, `loadTestsFromNames`, unittest CLI and pytest.
Pytest IDs come from collected class/method metadata after deselection, never
from a custom parser. Unittest verbose labels are checked against known method
metadata. Subtest outcomes are checked for failure but are not extra logical IDs.
Zero selection is checked separately: bare API success is deliberately observed,
and CLI exit codes are recorded rather than assumed portable. Explicit invalid
names must fail in both CLIs (unittest 1, pytest 4).

Each selection uses 21 alternating-order samples of discovery and named loading
in one process (168 suite executions including 42 empty suites). Tests are
imported before timing; load and total time are measured separately. Full named
loading loads the module once, while single/multiple loading uses exact names.
Empty named loading receives an empty list; its cost is not an optimization
comparison with directory discovery. External CLI startup times are excluded
from the API medians. Host contention can affect measurements; no CI speedup
or equivalence to a full project run is claimed.

Measured on Linux / CPython 3.12.3 / pytest 9.2.0 at baseline `600cfc588d25d60efb12b5460e0667bb4c27a11a`.

| Selection | API | Median load ms | Median total ms |
|---|---|---:|---:|
| all | discover | 1.384 | 79.513 |
| all | names | 0.231 | 72.902 |
| single | discover | 3.052 | 8.084 |
| single | names | 0.573 | 9.548 |
| multiple | discover | 6.363 | 23.033 |
| multiple | names | 2.041 | 14.311 |
| zero | discover | 2.556 | 2.572 |
| zero | names | 0.002 | 0.004 |

All/single/multiple select 27/1/2 tests. Both CLIs exit 5 for zero on this interpreter.
These host-dependent observations do not establish a repeatable runtime benefit.
Named loading saves discovery work; total times and host contention prevent
a fleet-wide speedup claim. Keep the existing CI execution commands.


Validation covers these focused subjects, not the entire project or all CI
Python versions. No dependency or production runtime changes are included.
