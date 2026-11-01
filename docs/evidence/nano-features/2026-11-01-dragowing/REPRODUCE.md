# Reproduce the bounded DRAgoWing HTML comparison

This is a source/fixture staging recipe, not an instruction to rerun production
CI, fetch arbitrary current branches, open HTML, or start Chromium. Versions are
the measured fixed versions; no upgrade is required. Keep package setup and
timed workloads exclusive of other benchmarks.

## Exact source staging

Place `benchmark_dra_html.py`, its harness tests, `source-receipt.json`,
the recorded synthetic receipt as `synthetic-fixtures.expected.json`,
`test-input-receipt.json`, and the two hash-locked
requirements files in a new experiment directory. Do not reuse install caches
or prior output directories. The script's `BASE` is its own directory. Its
measured uv executable was 0.13.4. The measured interpreter was CPython 3.12.14,
Linux x86_64; changing platform/interpreter is a new experiment, not a replay.

Materialize the following immutable repository revisions into temporary Git
checkouts (not under the measured venv directories):

| Checkout name | Repository | Commit |
|---|---|---|
| dragowing | myon-bioinformatics/DRAgoWing | be2185b142aebcbedee97688fe08ee0092179472 |
| pythdragoras | myon-bioinformatics/PythDRagoras | 6985419e89b3c02415b92bcc271e892946feb41b |
| queryu | myon-bioinformatics/QueRyu | 4c59cb00ca2ba898573e4af95bc4a63492aa5eb9 |
| aoi | myon-bioinformatics/Aoi | 385e14a09ad56b69c57ec1cbae04c16299d387c5 |

For each table row, with `name`, `repository`, and `commit` set literally:

```sh
git init "checkouts/$name"
git -C "checkouts/$name" remote add origin "https://github.com/$repository.git"
git -C "checkouts/$name" fetch --depth=1 --no-tags origin "$commit"
test "$(git -C "checkouts/$name" rev-parse FETCH_HEAD)" = "$commit"
```

For every `files[]` row in `source-receipt.json` that has `repository`, export
the exact tracked bytes using `git -C checkouts/NAME show REVISION:PATH` to the
row's relative `destination`. Do not use working-tree line-ending conversion.
Check SHA-256, byte count and Git blob identity against that row before using it.
Rows without `repository` are the two already supplied locked requirement
exports; their SHA-256 must match. The `source/dragowing` directory name is
intentional: it is the namespace package consumed by `python -m dragowing`.
No empty placeholder file may stand in for any input.

The exact export/check loop (run from the experiment directory after fetching
the four table rows) is:

```sh
python3 -S - <<'PY'
import hashlib, json, pathlib, subprocess
names = {
    'myon-bioinformatics/DRAgoWing': 'dragowing',
    'myon-bioinformatics/PythDRagoras': 'pythdragoras',
    'myon-bioinformatics/QueRyu': 'queryu',
    'myon-bioinformatics/Aoi': 'aoi',
}
for row in json.loads(pathlib.Path('source-receipt.json').read_text())['files']:
    destination = pathlib.Path(row['destination'])
    if 'repository' in row:
        data = subprocess.check_output([
            'git', '-C', 'checkouts/' + names[row['repository']],
            'show', row['revision'] + ':' + row['path'],
        ])
    else:
        data = destination.read_bytes()  # supplied locked export, not regenerated
    assert len(data) == row['bytes']
    assert hashlib.sha256(data).hexdigest() == row['sha256']
    if 'git_blob' in row:
        header = b'blob ' + str(len(data)).encode() + b'\0'
        assert hashlib.sha1(header + data).hexdigest() == row['git_blob']
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
helper = json.loads(pathlib.Path('test-input-receipt.json').read_text())
data = subprocess.check_output(['git', '-C', 'checkouts/aoi', 'show',
                                helper['revision'] + ':' + helper['path']])
assert hashlib.sha256(data).hexdigest() == helper['sha256']
assert len(data) == helper['bytes']
pathlib.Path('source/aoi_paths.py').write_bytes(data)
PY
```

Only these real research fixtures are required (from the Aoi commit above):

```text
cycles/c001-chunichi/outputs/season.jsonl       -> fixtures/c001/season.jsonl
cycles/c001-chunichi/outputs/propositions.jsonl -> fixtures/c001/propositions.jsonl
cycles/c001-chunichi/outputs/sets.jsonl         -> fixtures/c001/sets.jsonl
```

The source receipt includes their exact blob/SHA-256/size. The 168-row real
season input is not regenerated or edited. Native baseline imports the pinned
PythDRagoras `propositions.py` and `rule_search.py`; registration labels import
the pinned QueRyu `ask/ask.py`. These are source dependencies outside venv bytes.

For the unchanged forms test, separately export Aoi `aoi_paths.py` to
`source/aoi_paths.py`, verifying `test-input-receipt.json`. It was absent in the
first local test attempt, which is retained as a setup failure. This helper is
not needed by the measured HTML CLI and is not counted as an installed package.

The requirement exports derive from DRAgoWing `pyproject.toml` blob
`ca7cd1f307080d4c369a5a14031ee2cb4f93f14d` and `uv.lock` blob
`2788b84741af5ec04d59e5074e4010e067495654`, using the parent's locked offline
feature exporter. Feature output is Markdown 3.11.2 only; baseline has runtime
plus dev requirements. The original lock, dependency versions and tests stay
unchanged. Hashes in the supplied requirement files must not be regenerated
from newer registry metadata.

## Preparation and execution

```sh
python3 -B -S test_benchmark_dra_html.py
python3 -B -S benchmark_dra_html.py prepare
cmp synthetic-fixtures.expected.json synthetic-fixtures.json
python3 -B -S benchmark_dra_html.py verify
```

The measured harness has a literal `UV` path for the installed uv 0.13.4 binary.
On another host, point that constant at an independently provisioned uv 0.13.4
and record the resulting harness SHA as a new-run variation; do not claim the
modified runner has the original measured SHA. The harness does not install uv
or Python. A parent-maintained portable runner may expose this as an argument,
but must preserve the raw measured runner separately.

After obtaining the same scoped package-download authorization and an exclusive
measurement window:

```sh
python3 -B benchmark_dra_html.py install --exclusive-window
python3 -B benchmark_dra_html.py workloads --exclusive-window --environment-trial 3
```

In the recorded run, the third baseline install timed out at official PyPI's
Pygments endpoint. No retry or alternate host was used, and third feature was
not started. With explicit approval, the already successful matched trial **2**
was selected for offline work instead:

```sh
python3 -B benchmark_dra_html.py workloads --exclusive-window --environment-trial 2
```

That is a selected existing environment, not a successful third install. The
retained install comparison is **two completed pairs**, not three. An initial
escalated-sandbox startup failure is a separate infrastructure nonexecution;
the ordinary-sandbox run is the only executed installation command.

Run the unchanged offline test subset only after timing ends, in the successful
baseline venv (never add pytest to the small feature venv):

```sh
AOI_ROOT="$PWD/source" \
PYTHONPATH="$PWD/source:$PWD/source/dragowing:$PWD/source/pythdragoras:$PWD/source/queryu/ask" \
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
POLARS_MAX_THREADS=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
install-trials/2-default/venv/bin/python -m pytest -q -p no:cacheprovider \
  --confcutdir source/dragowing --basetemp offline-recheck-tmp \
  --junitxml offline-recheck-junit.xml \
  source/dragowing/tests/test_overlap_stdlib.py \
  source/dragowing/tests/test_forms.py \
  source/dragowing/tests/test_html_tag_count.py
```

This subset passed 141 tests after adding the verified layout helper; it is not
the full native/browser suite. No existing test was edited or skipped to make
the small environment pass. No `--embed`, `--png`, `--svg`, browser, Office,
Docker, or model operation is part of this experiment.

## Interpreting the retained evidence

`public-ready-evidence.json` contains all attempted install rows and the unstarted
row, raw per-batch/per-worker numbers, source identities and output hashes.
Workspace paths are explicit `<workspace>` placeholders, not runnable paths.
Original logs, receipt bytes, RSS samples and generated HTML remain untouched
under the local audit directory. Do not commit the multi-MB fixtures, complete
source snapshots, generated HTML, venvs or caches to parent #195.

The same-algorithm default/stdlib vs Markdown-only/stdlib bridge does not show a
runtime speed advantage from removing unused installed packages. The native vs
stdlib contrast is also an algorithm/import comparison. Repeated parallel
measurements are this two-task workload only, not a best-worker-count claim.
Read the explicit cache, shared-interpreter, logical disk size, sampled sum-RSS,
CLI-completion RSS high-water and browser-rendering limits in the JSON.
