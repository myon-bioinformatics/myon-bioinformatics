# Ironmate PPTX nano comparison — 2026-11-01

This is a bounded feature experiment, not a replacement for Ironmate's full
test suite, a visual/Office oracle, or a general benchmark framework. No child
repository source is vendored here. Existing dependency versions and all
existing tests remain unchanged.

## Inputs and prerequisites

- Ironmate source commit: `af846ee7b1fd440b8379e2a682dfc7d40365b30e`.
- The parent `tools/nano_node.py` contains the exact 11-file Git-blob allowlist:
  package manifest/lock, five production render files, `pptx_spec.py`, two
  unchanged focused tests, and `LICENSE`. It verifies those bytes, not checkout
  HEAD or ancestry. An existing checkout or an exact source-file snapshot works.
- The observed host already had Python 3.12.14, Node 24.22.1 and npm 11.9.0.
  Their installation time/size is not included. The source requires Node >=24.
- The scripts use Python's standard library. Network access to the official
  npm registry is needed only for the explicitly gated installation command.
  No Docker, browser, Office or model process is used.
- `TEST_PYTHON`, used only for the original tests below, must identify an
  existing isolated interpreter with these exact test-tool versions:
  pytest 9.1.1, iniconfig 2.3.1, packaging 26.3, pluggy 1.7.0, pygments 2.21.0.
  These are pinned in Ironmate `uv.lock`, Git blob
  `a08b5a5ce417cc466b9e9b4d6992861320c16d3a`. This run reused the already-created
  DRA baseline test interpreter, with plugin autoload disabled. Its environment
  is excluded from **both** Node runtime size/time figures; it is not claimed
  to be a minimal Python environment. No additional test-tool download occurred.

## Stage outside both repositories

Set `PARENT` to the parent checkout and `IRON_SOURCE` to the exact Ironmate
source snapshot. Create a fresh work directory; do not execute the harness in
the child checkout or overwrite an earlier run.

```sh
set -eu
PARENT=/path/to/parent
IRON_SOURCE=/path/to/Ironmate-at-af846ee7
BUNDLE="$PARENT/docs/evidence/nano-features/2026-11-01-ironmate"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/nano-pptx-XXXXXXXX")
python3 -S - "$PARENT" "$BUNDLE" <<'PY'
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
cp "$BUNDLE/measure_node.py" "$BUNDLE/measure_node_offline.py" \
   "$BUNDLE/run_pptx_fixture.py" "$WORK/"
python3 -S - "$IRON_SOURCE" "$WORK" <<'PY'
import importlib.util
from pathlib import Path
import sys
source, work = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location('nano_node', work / 'prototype/tools/nano_node.py')
nano = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nano)
for name, data in nano.read_snapshot(source).items():
    path = work / 'ironmate-inputs' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
PY
python3 -S "$WORK/prototype/tools/nano_node.py" \
  --source "$WORK/ironmate-inputs" --output-dir "$WORK/prepared" \
  > "$WORK/measurement-prepared.txt"
```

The preparer runs official npm offline with `--package-lock-only`, in a private
directory with empty npm configuration and an isolated cache. It removes only
the root ExcelJS dependency. All 18 retained non-root lock records, including
version/resolved/integrity/path fields, must exactly match the original 198-record
lock. Both overrides (`image-size` 2.0.4 and `uuid` 14.0.3) remain. npm also changes
the lock's root name from `workspace` to `Ironmate` and copies the unchanged
Node engine into the root record; those two explicit metadata changes are
allowed, not mistaken for package upgrades. No package installation occurs in
this preparation step.

## Explicit installation and workload run

Reserve an exclusive measurement window: do not run DRA measurements, package
installations, or other CPU-heavy tests concurrently. This command performs
six actual installations, so review it before supplying both execution flags.

```sh
python3 -S "$WORK/measure_node.py" --execute --exclusive-window
RUN=$(cat "$WORK/latest-measurement.txt")
```

Order is baseline, feature, feature, baseline, baseline, feature. Each trial has
a fresh source copy, HOME, empty user/global npmrc, temporary directory and npm
cache. The command is `npm ci --ignore-scripts --no-audit --no-fund
--no-update-notifier --registry https://registry.npmjs.org/
--fetch-retries=0 --fetch-timeout=30000`, plus the per-trial config/cache paths.
The copied feature manifest and full lock projection are validated immediately
before installation. A failure stops the series; do not retry through alternate
hosts, proxies or a less restricted execution route. Preserve the failed trial.

`npm ls --all --parseable --offline` verifies the installed package paths.
Size means regular-file apparent bytes and allocated bytes separately under
`node_modules`, excluding directory/symlink metadata. Cache bytes are separate.
Installation wall time includes only the npm subprocess, not source staging or
offline preparation. Filesystem and remote CDN caches were not reset. All six
successful raw timings are retained, including the slower feature sample.

The workload runs two jobs in every batch, at concurrency 1 and 2, with three
repeats per environment and alternating environment order. Each job has private
output and temporary paths; dependencies/source are shared read-only, Python
bytecode writes are disabled. Timing includes Python/Node startup, generation,
file I/O and the structural oracle, not just rendering.

## Oracle and the preserved initial setup failure

Each job creates a Unicode/table/shape/line-plus-triangle deck, the same deck in
reordered display order, and a one-pixel PNG deck; it also checks rejection of
unsupported arrow styling. The independent standard-library oracle validates
ZIP member uniqueness, content types, all relationship targets and exact image
bytes. It compares every member's parsed XML or binary digest. Only ZIP archive
metadata and `docProps/core.xml` created/modified timestamp text are omitted.
The public-spec round trip is a secondary, same-implementation check—not an
independent visual-equivalence claim.

The initial run completed all six installs but failed the first within-baseline
image comparison: PptxGenJS's default image description included the worker's
absolute input path. The corrected **adapted** image fixture supplies explicit
stable `altText`; arbitrary XML descriptions are not normalized away. The
original image regression test is separately executed unchanged below.

The initial receipt, stdout/stderr and decks remain intact. A distinct offline
continuation reused the already installed final baseline/feature environments:

```sh
python3 -S "$WORK/measure_node_offline.py" --execute --exclusive-window \
  --installed-trials "$RUN"
OFFLINE=$(cat "$WORK/latest-offline.txt")
```

This continuation performs no install or network operation. It validates source,
manifests/locks and installed package versions first, and writes a fresh output
directory without overwriting the earlier failure. The supplied current fixture
already includes the correction, so a fresh main run normally completes directly;
the continuation documents the actual historical recovery, not a required retry.

## Run the two original focused tests unchanged

After timed work ends, verify the separate test interpreter and run both files
against each installed source tree. No tests are edited or deleted. Full child
CI, visual tests and unrelated optional features are outside this bounded check.

```sh
TEST_PYTHON=/path/to/existing/pinned-test-venv/bin/python
"$TEST_PYTHON" -c 'import importlib.metadata as m; expected={"pytest":"9.1.1","iniconfig":"2.3.1","packaging":"26.3","pluggy":"1.7.0","pygments":"2.21.0"}; assert {k:m.version(k) for k in expected} == expected'
for CASE in 5-baseline 6-feature; do
  PROJECT="$RUN/$CASE/Ironmate"
  TEST_OUT=$(mktemp -d "$WORK/original-tests-XXXXXXXX")
  mkdir "$TEST_OUT/tmp"
  (
    cd "$PROJECT"
    env -u PYTHONHOME -u PYTEST_ADDOPTS -u PYTEST_PLUGINS \
      -u NODE_OPTIONS -u NODE_PATH -u NODE_ENV \
      PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 \
      PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH="$PROJECT" TMPDIR="$TEST_OUT/tmp" \
      "$TEST_PYTHON" -m pytest tests/test_pptx_mapping.py tests/test_pptxgenjs_image.py \
      -q -rA -c /dev/null --rootdir "$PROJECT" --confcutdir "$PROJECT" \
      --noconftest -p no:cacheprovider --junitxml "$TEST_OUT/junit.xml"
  ) > "$TEST_OUT/stdout" 2> "$TEST_OUT/stderr"
  python3 -S - "$TEST_OUT/junit.xml" <<'PY'
from xml.etree import ElementTree as ET
import sys
suites = list(ET.parse(sys.argv[1]).getroot().iter('testsuite'))
assert sum(int(s.get('tests', 0)) for s in suites) == 8
assert all(int(s.get(k, 0)) == 0 for s in suites for k in ('failures', 'errors', 'skipped'))
PY
done
```

## Evidence scope

`evidence.json` contains all raw install samples and fixed-volume batch timings,
source/tool/fixture identities, test results and interpretation. `manifest.json`
hashes this compact bundle. The raw local receipts, logs and generated decks are
preserved unchanged; their receipt digests are recorded, while machine-local
paths and child source copies are not published.

Observed savings are dependency installation bytes. Install time varied; nano
did not materially improve serial workload time. Both environments supported
parallel jobs. These small samples are not a general performance guarantee.
