# Reproduce the selected MCP comparison

Do not install or start workers until the operator has authorization for this
specific existing-version package/local-mock experiment and an exclusive timing
window. The flags below document that gate; they do not grant permission.

## Stage exact source

Keep the two harness files, two fake-test files, source receipt and requirements
exports together. Obtain the immutable source commit through a permitted GitHub
read route (the preparation used connector Git tree/file reads, then reused only
local blobs whose Git SHA matched that tree). A normal Git example, not executed
as part of the comparison:

```sh
git init source-checkout
git -C source-checkout remote add origin https://github.com/myon-bioinformatics/mcp-toolcall-lab.git
git -C source-checkout fetch --depth=1 origin 06cdc8e4e14d50de45c8b8122a5ccff6911c3404
```

From the comparison directory, stage only the receipt-listed source files:

```python
import hashlib, json, pathlib, subprocess
root = pathlib.Path('.')
receipt = json.loads((root / 'source-receipt.json').read_text())
for row in receipt['files']:
    if row['repository_path'] is None:
        data = (root / row['local_path']).read_bytes()  # supplied locked export
    else:
        data = subprocess.check_output(['git', '-C', 'source-checkout', 'show',
            receipt['revision'] + ':' + row['repository_path']])
        assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == row['git_blob']
    assert len(data) == row['bytes']
    assert hashlib.sha256(data).hexdigest() == row['sha256']
    destination = root / row['local_path']
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
```

The requirements files are offline uv0.13.4 exports from exact pyproject blob
`4c3d89820b99fd11717ce0c454e0149226e8d8cd` and lock blob
`6fd1a4b3a927120d9684d5d3a4e3941de7b13415`, with no extras/default groups/project.
Full export uses no pruning; caller adds `--prune fastmcp`. Both supplied files
omit header/annotation comments. The parent `nano_export.py --feature
mcp-chat-client` output includes `# via` annotation comments but the same
requirement/marker/hash lines. Do not edit pins or remove SDK transitive packages.

## Required executable configuration for every reproduction

The published `measure_mcp.py` deliberately contains the literal placeholder
`UV = Path('<uv-0.13.4>')`; it cannot install packages as published. **Every
reproduction must configure it** to an independently existing, trusted uv0.13.4
executable. This step does not download or upgrade uv.

First verify every published payload against `MANIFEST.json` (file size and
SHA-256), then copy the verified bundle into a private working directory. Keep
the verified public bundle unchanged. In that working copy only:

1. Identify the absolute path of a trusted, already-installed uv executable.
2. Run `/absolute/path/to/uv --version` and require version `0.13.4`; if missing
   or different, stop rather than installing/upgrading a tool implicitly.
3. Edit only the `UV = Path('<uv-0.13.4>')` constant in `measure_mcp.py` to that
   verified absolute path.
4. Record `sha256sum measure_mcp.py` as the **new reproduction harness hash**.

The public placeholder copy, original measured orchestrator, and newly configured
working copy have distinct identities. Preserve the measured/public hashes in
`evidence.json`; never label the configured copy with the original measured hash.
The measurement commands below apply only to that configured working copy.

## Safe preparation checks

```sh
python3 -B -S prepare_mcp_comparison.py verify
python3 -B -S prepare_mcp_comparison.py plan
python3 -B -S -m unittest -v test_prepare_mcp_comparison.py test_measure_mcp.py
```

These modes install nothing and start no listener. The original catalog test can
be run with `PYTHONPATH=runtime-source/src:source python3 -B -S -m unittest -v
tests.test_catalog`; the retained original run additionally installed an audit
hook rejecting socket connects, DNS and subprocesses and checked that MCP and
FastMCP were not imported. The test file itself was unchanged.

## Authorized exclusive measurement window only

```sh
python3 -B measure_mcp.py install --exclusive-window
# Only after a successful matched pair, selecting its actual trial explicitly:
python3 -B measure_mcp.py workloads --exclusive-window --environment-trial 3
```

Installation plans at most6 attempts in AB/BA/AB order, stdlib venvs without pip,
fresh isolated uv caches, official PyPI only, hash-locked binary wheels,
`--no-deps --link-mode copy`. No upgrades, builds or interpreter download.
HTTP retries0; per-request timeout60s; operation timeout300s. A failure stops
later attempts. Do not retry by changing registry, proxy, sandbox privilege or
host. An incomplete trial must remain incomplete in reports. Offline use of an
earlier successful matched pair requires a documented selection; never silently
rename/repoint it as trial3.

`install-trials`/`workload-runs` must not exist initially: no silent overwrite or
automatic resume. The installer records every attempted stage, command, exit,
raw stdout/stderr, venv/cache apparent and allocated regular bytes and metadata.
Interpreter/stdlib and symlink targets are excluded from those bytes. Counts
come from actual metadata, not requirement rows. Cache bytes are not network
transfer. No optional test-tools install is part of this plan.

Workloads use only the local deterministic mock fixture, not an external API or
LLM. Raw full client results, server JSONL, each batch receipt and process exit
statuses remain available. On any parity/lifecycle failure, diagnose preserved
evidence; do not weaken the gate or drop output fields to make it pass. See
`SOURCE-BOUNDARY.md` for fixture accounting, cases and evidence limits.
