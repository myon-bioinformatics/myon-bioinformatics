# Focused standard-library test discovery

Run from this repository root with Python 3.11 or newer. These commands use
Python’s built-in test runner; the listed checks need no third-party package
or browser installation. `-S` disables automatic `site` initialization and its
usual site-packages path additions; it is not a complete isolation boundary.

```sh
python -S -m unittest discover -s tests -p test_runtime_plan.py -v
```

Validated on Linux / CPython 3.12.14: **27 tests, exit 0** (2026-11-03).

To narrow the first command to a test name, use the built-in `-k` filter:

```sh
python -S -m unittest discover -s tests -p test_runtime_plan.py -v -k test_examples_match_only_declared_requirements
```

`-s` is the discovery start directory, `-p` matches test filenames, `-k`
filters test names, `-v` prints each executed test, and `-f` stops at the first
failure. Quote filename patterns so the shell does not expand them. Check the
final `Ran N tests` line: zero tests is not a successful verification, even
though unittest can exit 0. `-k` runs selected tests; it is not a list-only mode.

These are focused checks, not the complete project suite. Keep the existing
pytest/CI command for full coverage and native JUnit or fixture support.
Unittest does not collect module-level pytest functions or apply pytest
fixtures, parametrization, markers, conftest hooks or configured pythonpath.
A broad `test*.py` pattern can import pytest-only modules and fail under `-S`.
Do not add an extra CI pass over tests already owned by pytest just to expose
this local command. No custom discovery runner is required.

## Cross-repository audit (2026-11-03)

The 2026-11-03 audit covers 24 public, non-archived repositories, including
this parent repository. The [2026-10-21 use-case audit](cross-repository-use-cases.md)
covered 23 repositories and also included this parent; this audit additionally
includes TalkCompress. Sources were checked out from their default branches;
the short revisions below identify the snapshots and are not vendor pins or
a live CI registry.

On Linux / CPython 3.12.14, 21 focused commands across 18 repositories completed
with exit 0, totaling 435 reported test cases, including skips
(Aoi/subtree checks may overlap).
An additional name-filtered command was verified in each of those repositories.
This is not full-suite or cross-version validation.

| Repository | Snapshot | Verified stdlib discovery command(s) / disposition |
|---|---|---|
| Aoi | `94fd7c8c6cce` | `python -S -m unittest discover -s tests -p test_ci_status_label.py -v` — 15 tests<br>`python -S -m unittest discover -s nagoyaction/tests -p 'test_c001_*.py' -v` — 72 tests |
| BlueProbe | `2ea993aaa141` | No unittest.TestCase classes found in the inspected test files. Preserve pytest; use its `--collect-only -q` after preparing the existing project dependencies and Aoi prerequisites where required. Not executed in this audit. |
| DRAgoWing | `07d286155501` | `python -S -m unittest discover -s tests -p test_dragowing_managed_chromium.py -v` — 10 tests |
| Err-o-RoadRunner | `ff947a32ffdf` | No unittest.TestCase classes found in the inspected test files. Preserve pytest; use its `--collect-only -q` after preparing the existing project dependencies and Aoi prerequisites where required. Not executed in this audit. |
| Ironmate | `3b7c07d06551` | `python -S -m unittest discover -s tests -p test_adaptive_fingerprint.py -v` — 5 tests |
| NagoyAction | `da68f374ad1a` | Aoi integration required. Standalone `test_c001_*.py` import fails; the same pattern passes 72 tests in Aoi under `-s nagoyaction/tests`. Keep the existing Aoi checkout/overlay CI setup. |
| PythDRagoras | `9fb998c6db1b` | No unittest.TestCase classes found in the inspected test files. Preserve pytest; use its `--collect-only -q` after preparing the existing project dependencies and Aoi prerequisites where required. Not executed in this audit. |
| QueRyu | `4c59cb00ca2b` | No unittest.TestCase classes found in the inspected test files. Preserve pytest; use its `--collect-only -q` after preparing the existing project dependencies and Aoi prerequisites where required. Not executed in this audit. |
| SakAnalytics | `95cfacd4f8ae` | No unittest.TestCase classes found in the inspected test files. Preserve pytest; use its `--collect-only -q` after preparing the existing project dependencies and Aoi prerequisites where required. Not executed in this audit. |
| TalkCompress | `13bc33517ef7` | `python -S -m unittest discover -s tests -p 'test_session_*.py' -v` — 10 tests |
| ascii_artist | `a9af65cc443e` | `python -S -m unittest discover -s tests -p 'test_flow_*.py' -v` — 19 tests |
| browser-test-kit | `cc7322bc2e0a` | `python -S -m unittest discover -s tests/stdlib -p 'test*.py' -v` — 4 passed, 1 skipped<br>`python -S -m unittest discover -s tests/python -p 'test_nano_bun*.py' -v` — 49 tests |
| cli_args | `5059570cdc0c` | `python -S -m unittest discover -s tests -p test_runtime_compatibility.py -v` — 3 tests |
| flutter_navigation_basic | `39d659ebffa5` | `python -S -m unittest discover -s python/tests -p test_flutter_focused_ci_contract.py -v` — 4 tests<br>`python -S -m unittest discover -s .github/scripts -p test_ci_status_label.py -v` — 13 tests |
| gh_identity | `1dd9b6041c1c` | `python -S -m unittest discover -s tests -p test_gh_identity.py -v` — 66 tests |
| markdown | `8590570dd5af` | `python -S -m unittest discover -s tests -p test_runtime_compatibility.py -v` — 3 tests |
| mcp-toolcall-lab | `d66e2e3eb6f2` | `python -S -m unittest discover -s tests -p test_runtime_trust.py -v` — 44 tests |
| myon-bioinformatics | `4a765f6e1da7` | `python -S -m unittest discover -s tests -p test_runtime_plan.py -v` — 27 tests |
| myon-bioinformatics.github.io | `772c88aaa4d6` | `python -S -m unittest discover -s tests -p test_portable_playwright.py -v` — 9 tests |
| nvd_nist_known_vulns | `e479242df58b` | `python -S -m unittest discover -s tests -p test_nvd_client.py -v` — 45 tests |
| search_seq_including_spaces | `dfe5303ed883` | `python -S -m unittest discover -s tests -p test_sequence_readers.py -v` — 15 tests |
| web-ui | `ff4c0caf9f90` | `python -S -m unittest discover -s tests -p test_embedded_modules.py -v` — 6 tests |
| xprobe | `1ffbb7b98407` | `python -S -m unittest discover -s tests -p test_portable_output.py -v` — 11 tests |
| yourself | `1a0f61358917` | `python -S -m unittest discover -s tests -p test_diagnose_runtime.py -v` — 4 tests |

### Findings and limits

- Several roots have no `tests/__init__.py`; an unqualified root discovery can report zero tests. Explicit `-s` avoids depending on package recursion. Adding `__init__.py` indiscriminately changes import identities and is unnecessary for the listed commands.
- Ironmate `test_studio_*.py` also imports pytest-only files and fails under `-S`; the narrower adaptive-fingerprint recipe above passes.
- mcp-toolcall-lab `test_portable_*.py` imports pytest-only files, misses src-path setup and hits a local runtime capability mismatch. Use the offline `test_runtime_trust.py` recipe above; full portable tests remain under their existing environment.
- NagoyAction’s standalone replay tests depend on Aoi parent scripts. The existing CI overlays the subtree into Aoi; this audit does not bypass that integration boundary or silently skip it.
- README examples in web-ui and ascii_artist that suggested unrestricted discovery have been narrowed to existing stdlib modules.
- The recipes expose already useful focused tests. They add no dependency, test conversion, custom scanner, extra CI execution, or claim of reduced runtime. Existing pytest collection, JUnit, failure identities, locks and CI ownership remain authoritative.

For pytest-style tests, unittest is not a general replacement. In the configured
project environment, append `--collect-only -q` to the existing pytest command
to list cases without running test bodies (collection still imports modules
and can execute collection hooks). Use pytest’s `-k` for name selection.
Unittest discovery imports modules and executes collected tests. Neither is
a side-effect-free source search.

## In-process selection follow-up

The original CLI audit remains unchanged. A repository-local follow-up now
validates standard `TestLoader` selection on `RuntimePlanTests`, with a matching
trial on cli_args's audited stdlib compatibility tests. See
[testloader-selection-experiment.md](testloader-selection-experiment.md) for the
executable recipe, error/zero guards, parity evidence and measurements.
No common runner, source cache, additional CI job or fleet rollout is added;
no runtime improvement has been established.
