# `pytest_brief.py`

`pytest_brief.py` is a stdlib-only parent tool (ported from `browser-test-kit`). It installs only the
missing test dependencies and prints just the important part of a pytest run: failures, coverage and
the final counts. Consumer repositories vendor this file from here.

## What it does

1. **Minimal dependency install**
   - Reads `--requirements` (default: `tests/requirements.txt` when it exists).
   - Uses the target Python's `pip` (>= 23.0) `--dry-run --report` to find packages that are not
     installed yet, so nothing is reinstalled needlessly.
   - Detects PEP 668 `EXTERNALLY-MANAGED` interpreters and adds `--break-system-packages` only there
     (never inside a venv).
2. **Run pytest and extract the summary**
   - Runs `python -m pytest -q -rfE -p no:cacheprovider`.
   - Prints only `FAILED` / `ERROR` lines, coverage lines (`TOTAL ...`, `Required test coverage ...`)
     and the final summary line (`X passed, Y failed in ...`).
   - When pytest itself could not run (exit 2-5 with no summary line), prints the last `--tail` lines.
3. **Stdlib only**: runs as `python -S pytest_brief.py`, loosely coupled to the pytest and pip it drives.

## Usage

Separate `pytest_brief.py` options from pytest's own options with `--`:

```sh
python -S pytest_brief.py [pytest_brief_options] -- [pytest_options]

python -S pytest_brief.py -- --cov=xprobe --cov-branch --cov-report=term --cov-fail-under=90
python -S pytest_brief.py --requirements tool/python/requirements.txt -- tool/python/tests
```

### Options

- `--requirements PATH`: dependency file (default: `tests/requirements.txt` if present).
- `--tail N`: number of trailing output lines shown when something fails without a summary (default 15).
  It applies to both cases: a failed `pip install` (its stdout and stderr) and a pytest run that did
  not start or finish normally.
- `--python PATH`: interpreter used for pip and pytest (default: `sys.executable`).
- `--json`: print `{ok, exit_code, installed, lines, hint}` instead of text lines.
  Exit 3 still means a requirement failed and pytest did not run. The pip tail stays on stderr.

## Exit status

- `0`: all tests passed (or were skipped)
- `1`: test failures
- `3`: dependency analysis or installation failed
- anything else: pytest's own exit code, passed through
