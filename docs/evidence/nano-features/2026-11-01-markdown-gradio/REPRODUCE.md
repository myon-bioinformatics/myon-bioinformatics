# Reproduce the source-bound Gradio comparison

Only the selected text-input/API feature is in scope. Package downloads require
authorization for existing locked packages; local workers require authorization
and an exclusive timing window. No browser/model/Docker/live API is part of this
recipe. Do not run the original child-wide/browser suite as an implied next step.

## Verify and stage a private copy

Verify every published file's byte count and SHA-256 against `MANIFEST.json`, then
copy the verified bundle to a private working directory. Keep the published
bundle unchanged. Its `measure_gradio.py` contains literal `<uv-0.13.4>` in the
UV constant: **every reproduction must configure that placeholder** to a trusted,
already-installed uv0.13.4 executable. Check `/absolute/path/to/uv --version`,edit
only the constant in the working copy,then record `sha256sum measure_gradio.py`
as the new reproduction hash. Missing/different uv means stop,not download or
upgrade it implicitly. Never call the configured copy the original measured
harness; measured/public hashes remain distinct in the evidence.

Use an existing trusted curl8.14.1 at `/usr/bin/curl` for the unchanged curl tests.
The recorded host binary identity is in `host-curl.json`. Another path/build or
runtime is a changed reproduction environment and must be disclosed; do not
loosen the command/endpoint allowlist to make a test pass. The original run used
CPython3.12.14,Linux x86_64; all Python packages still use the supplied lock hashes.

## Exact child source

Obtain immutable commit `f04d233d2edc63650439fbb7692434e9f2c66266` from public
`myon-bioinformatics/markdown` through a permitted read route. Preparation used
connector Git-tree/file reads and reused only local bytes matching Git blobs.
An ordinary Git equivalent (not used as an install fallback) is:

```sh
git init source-checkout
git -C source-checkout remote add origin https://github.com/myon-bioinformatics/markdown.git
git -C source-checkout fetch --depth=1 origin f04d233d2edc63650439fbb7692434e9f2c66266
```

From the private comparison directory:

```python
import hashlib, json, pathlib, subprocess
root = pathlib.Path('.')
receipt = json.loads((root / 'source-receipt.json').read_text())
for row in receipt['files']:
    if row['repository_path'] is None:
        data = (root / row['path']).read_bytes()  # supplied locked export
    else:
        data = subprocess.check_output(['git', '-C', 'source-checkout', 'show',
            receipt['revision'] + ':' + row['repository_path']])
        assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == row['git_blob']
    assert len(data) == row['bytes']
    assert hashlib.sha256(data).hexdigest() == row['sha256']
    path = root / row['path']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
```

Both requirements exports are from exact pyproject blob
`031f0def5bebc211a37b85d6ab769ac27721f86e` and uv.lock blob
`39599c712c22eddc9b2d42bd0f0a13cab90b7f38`. Common offline uv0.13.4 options:
`--no-config export --project SOURCE --offline --locked --no-python-downloads
--python PYTHON --no-default-groups --no-emit-project --no-header --format
requirements.txt --group frontend`. The feature adds `--prune streamlit --prune
playwright`; no versions/hashes are changed. Parent helper feature
`markdown-gradio` exports this reviewed feature closure. No wheel build/project
installation is necessary for the source-intact demo.

## Separately accounted test tools

The original run reused exact lock-matching pytest9.1.1,iniconfig2.3.1,pluggy1.7.0
from a previously successful hash-locked isolated environment. It copied only
121 receipt-listed files (1,454,604bytes),checking each installed RECORD hash;
no executable scripts/bytecode or unrelated packages were copied. Packaging26.3
and Pygments2.21.0 are already in both runtime closures. Reuse an independently
existing matching installation:

```sh
python3 -B -S stage_test_tools.py --site-packages /trusted/existing/venv/lib/python3.12/site-packages
```

This helper only validates/copies receipt-matching bytes; it never installs or
uses the network. If they are absent/different,stop and arrange separately
authorized exact test-tool staging,or record the new tool identity for a changed
experiment. Never expose an old environment's whole site-packages through
PYTHONPATH: that would hide missing runtime dependencies. Test files stay intact;
Pytest autoload is disabled and all4 tests must pass with zero skips.

## Safe checks, then separately authorized execution

```sh
python3 -B -S gradio_worker.py verify
python3 -B -S -m unittest -v test_harness.py
# Authorized exclusive window only; verify adequate disk before starting:
python3 -B measure_gradio.py install --exclusive-window
# Only for an actually completed matched pair, selected explicitly:
python3 -B measure_gradio.py workloads --exclusive-window --environment-trial 1
```

The published result used trial1 **because trial2's feature install failed** at
the official NumPy wheel connection timeout. No retry/fallback was performed.
Only1 successful paired install sample exists,not3; trial2 full and both trial3
installs never started. The runtime phase reused matchedtrial1 for3 repetitions
and ran all4 original tests in each profile. A fresh reproduction's actual
successful trial must be selected explicitly,not inferred from this example.

Install mode plans at most6 fresh caches/venvs in AB/BA/AB order,private
HOME/config/tmp,hash-locked binary wheels,official PyPI,`--no-deps --link-mode
copy`,HTTP retries0/request60s/operation300s. First failure stops later attempts.
Do not change host,proxy,sandbox privilege or registry to retry a denial/failure.
Existing output directories are refused,not overwritten or silently resumed.

Runtime mode includes12 batches with2 fixed jobs each; each owns a private app
and loopback port. It calls real queue/API routes for3 fixtures and preserves all
four output fields. The4 original no-browser tests then run unchanged in both
profiles. Raw job IDs/HTTP/SSE bodies,audit logs,stdout/stderr,receipts and JUnit
remain local. Source/export/tool/harness identities are checked around every
stage. Any failed/timeout/skip remains failure; do not normalize away differences
or weaken egress gates. See `PLAN.md` and `RESULTS.md` for boundaries/accounting.
