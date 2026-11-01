# Browser-test-kit document mirrors — 2026-11-01

This bounded experiment keeps both independent Markdown parsers, the HTML
reader, and the existing test runner. It is not the full browser-test-kit suite.
No child source, package installation, browser binary or raw machine-local log
is included in this bundle. All existing child tests remain unchanged.

## Inputs and identity

- Child commit: `111f6d78befae28acfb198a45b72e5aa0e59dcde`.
  `source-receipt.json` identifies the nine required files by Git blob, SHA-256
  and immutable public URL. The helper verifies this bounded snapshot, not
  checkout HEAD or ancestry. Use an existing checkout or exact file snapshot.
- Use a parent checkout containing this bundle whose helper and tests match
  `manifest.json`. The publication commit is recorded in the parent PR.
  **Do not blindly use the latest helper**, bypass its hash checks, or relabel
  a newer helper as the one measured here.
- The observed host already had Python 3.12.14, Node 24.22.1 and npm 11.9.0;
  Node >=24 is required by the child. Runtime installation is not measured.
  The harness itself uses Python's standard library.
- Official npm registry access is needed only for the gated installation run.
  No Docker, browser engine, external API or model process is used. Installing
  the Playwright test-runner package is not installing or launching a browser.

## Verify and stage outside both repositories

Set `PARENT` and `BTK_SOURCE` to existing local sources, then create a fresh
private work directory. Do not execute the harness in either source checkout
or overwrite an earlier run.

```sh
set -eu
PARENT=/path/to/parent-with-matching-bundle
BTK_SOURCE=/path/to/browser-test-kit-at-111f6d78
BUNDLE="$PARENT/docs/evidence/nano-features/2026-11-01-browser-test-kit"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/nano-btk-XXXXXXXX")
python3 -B -S - "$PARENT" "$BUNDLE" <<'PY'
import hashlib, json, sys
from pathlib import Path
parent, bundle = map(Path, sys.argv[1:])
manifest = json.loads((bundle / 'manifest.json').read_text())
for name, digest in manifest['sha256'].items():
    assert hashlib.sha256((bundle / name).read_bytes()).hexdigest() == digest, name
for name, digest in manifest['parent_files_sha256'].items():
    assert hashlib.sha256((parent / name).read_bytes()).hexdigest() == digest, name
PY
mkdir -p "$WORK/prototype/tools"
cp "$PARENT/tools/nano_node.py" "$WORK/prototype/tools/nano_node.py"
cp "$BUNDLE/measure_btk.py" "$BUNDLE/run_btk_fixture.py" \
   "$BUNDLE/test_run_btk_fixture.py" "$WORK/"
python3 -B -S - "$BTK_SOURCE" "$WORK" <<'PY'
import importlib.util, sys
from pathlib import Path
source, work = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location('nano_node', work / 'prototype/tools/nano_node.py')
nano = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nano)
for name, data in nano.read_snapshot(source, blobs=nano.BTK_SOURCE_BLOBS).items():
    path = work / 'btk-inputs' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
PY
python3 -B -S "$WORK/prototype/tools/nano_node.py" \
  --feature browser-test-kit-document-mirrors \
  --source "$WORK/btk-inputs" --output-dir "$WORK/prepared" \
  > "$WORK/measurement-prepared.txt"
(cd "$WORK" && python3 -B -S -m unittest test_run_btk_fixture -v)
```

Preparation uses official npm offline in a fresh private directory with empty
configuration and `--package-lock-only --ignore-scripts`. It removes only the
root ExcelJS dependency, retaining `@playwright/test`, `linkedom`, `markdown-it`
and `marked`. All 30 retained non-root records from the original 221-record
lock must match in full, including path, version, resolved URL and integrity.
The original `uuid` and `minimatch` overrides remain. Only the lock root name
changes from `dep-browser-test-kit` to the unchanged manifest's
`browser-test-kit`; no package is upgraded. The sole allowed optional peer is
the original `linkedom` declaration for `canvas >= 2`; canvas remains absent.
Other peer/optional/link/install-script graph features fail closed. A separate
27-record projection without the test runner was analyzed offline, **not
installed or measured**.

## Explicit bounded install and workload comparison

Reserve an exclusive measurement window before supplying both execution flags.
Do not run other installs or CPU-heavy workloads concurrently. This performs
six actual npm installations and then twelve workload batches; inspect it first.

```sh
python3 -B -S "$WORK/measure_btk.py" --execute --exclusive-window
RUN=$(cat "$WORK/latest-measurement.txt")
```

Order: baseline, feature, feature, baseline, baseline, feature. Each install
gets a fresh HOME, cache, temporary directory and empty user/global npmrc.
The exact command is `npm ci --ignore-scripts --no-audit --no-fund
--no-update-notifier --registry https://registry.npmjs.org/
--fetch-retries=0 --fetch-timeout=30000`, plus private config/cache paths,
with `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`. Source and the complete projected
lock are validated again immediately before every install. A registry or
validation failure stops the series and preserves the partial receipt/logs;
do not retry via another host, proxy or permission route.

`npm ls --all --parseable --offline` must find exactly the reviewed lock paths.
Installed bytes mean regular-file `st_size` and allocated `st_blocks * 512`
under `node_modules`, excluding symlink/directory metadata. Cache bytes are
separate, not network-transfer measurements. Install wall time covers the npm
subprocess, not preparation or source staging. Filesystem and CDN caches were
not reset. All six installs succeeded in this run, including the first feature
sample that was slower than its paired baseline. There were no retries or
fixture corrections.

There are always two jobs per batch, at concurrency one and two, with three
repeats per environment and alternating environment order: 12 batches/24 jobs.
Each job has private source stage, output, reports, HOME/cache, temporary paths
and an asserted-empty browser directory. Staged source manifests/tests are the
unchanged originals; `node_modules` is a read-only-use symlink to the selected
installation. **Never run npm in this worker stage**: its original manifest
does not claim that the projected install contains all original dependencies.
No `npx` auto-fetch occurs; the installed test CLI is invoked directly.

Each job checks full facts from six Markdown cases through both parsers,
explicit HTML facts, four expected rejection cases, and two unchanged original
Node tests. Unicode, entities, code blocks and links are included. The deliberate
CommonMark/GFM bare-URL disagreement must remain a disagreement; it is not
normalized away. `full-facts-reference.json` contains the complete canonical
reference once, while `evidence.json` retains every batch and worker hash.
**Two unique original tests repeated 24 times means 48 test executions, not
48 unique tests.** There are zero skips/failures. The original ExcelJS/XLSX
Node case and real-browser lanes are untouched but not run in this slice.
Timing includes process startup, staging, I/O, assertions and the test runner,
not only parser functions. Both installations already support parallel jobs;
this run does not demonstrate a serial-workload speed improvement.

## Original Python comparator tests, outside Node measurements

After timing, the unchanged `tests/python/test_mirror_check.py` produced
54 passes and zero failures/errors/skips (including its standard-library XLSX
checks). This reused an existing DRA test interpreter without downloading more
packages. That chosen tool environment is **not claimed to be selected by the
BTK lock**, and is excluded from both Node runtime byte/time figures.

To reproduce this separate check, set `TEST_PYTHON` to an existing interpreter
with the exact observed test-tool versions below. This is not an installation
recipe or a claim that the test environment is minimal.

```sh
TEST_PYTHON=/path/to/existing/test-venv/bin/python
"$TEST_PYTHON" -I -B - <<'PY'
from importlib.metadata import version
expected = {'pytest':'9.1.1', 'iniconfig':'2.3.1', 'packaging':'26.3',
            'pluggy':'1.7.0', 'pygments':'2.21.0'}
assert {name:version(name) for name in expected} == expected
PY
(cd "$WORK/btk-inputs" && env -u PYTHONPATH -u PYTHONHOME \
  PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  "$TEST_PYTHON" -B -m pytest tests/python/test_mirror_check.py \
  --noconftest -p no:cacheprovider --junitxml="$WORK/comparator-junit.xml" -q)
python3 -B -S - "$WORK/comparator-junit.xml" <<'PY'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
counts = {key:sum(int(s.get(key, '0')) for s in suites)
          for key in ('tests', 'failures', 'errors', 'skipped')}
assert counts == {'tests':54, 'failures':0, 'errors':0, 'skipped':0}, counts
PY
```

The public evidence is compact derived data plus unchanged measured harnesses.
Raw local receipts, command stdout/stderr and JUnit remain preserved; their
audit digests are recorded without publishing machine-local paths. This is
facts/schema/test parity, not arbitrary document rendering, browser behavior,
full-repository equivalence, image-size measurement or a general speed claim.
