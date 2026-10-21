# Discover files and production entry points by convention

Find lock validation, identity verification and pin collection entry points across parent tooling.

Use a sibling xprobe checkout with the convention-discovery API from
[xprobe PR #28](https://github.com/myon-bioinformatics/xprobe/pull/28).
For a fresh checkout, select this tested revision:

```sh
git clone https://github.com/myon-bioinformatics/xprobe.git ../xprobe
git -C ../xprobe checkout --detach 045b54dd35baf43758a484e458712ed5ad47ba24
```

The commands below run from this repository root. If xprobe is elsewhere,
replace `../xprobe/xprobe.py` with that checkout's path. They use Python's
standard library and work with `python -S`.

```sh
python -S ../xprobe/xprobe.py --symbols . --include vendor_sync.py --include 'tools/*.py' --symbol-pattern 'validate*' --symbol-pattern 'verify*' --symbol-pattern 'collect*' --brief
python -S ../xprobe/xprobe.py --discover --root . --include '*.yml' --include '*.toml' --include-hidden --brief
```

Repeat `--symbol-pattern` or `--include` to inject different naming conventions;
alternatives are ORed. `--symbol-kind class` (also `function`, `async_function`)
filters the selected definitions. For example, replace the name rules with
`--symbol-pattern '*Adapter' --symbol-pattern '*Reader' --symbol-kind class`
to look for class families. There is no required `tests/` path or `test_*` prefix.

Filename patterns match basenames or relative paths; symbol patterns match
short or lexical dotted names. Both are case-sensitive. Wildcard `*` spans
slashes/dots, so a parent-name match also selects nested definitions. Repeated
source definitions remain separate. This is a source inventory, not a count of
callable runtime members or an import/plugin registration mechanism.

JSON is the default for further processing; omit `--brief`. Exit status 0 means
complete matches, 1 means none, and 2 means errors, skipped inputs or truncated
output. Default bounds are 5,000 matched files, 1 MB per source and 1,000 symbols;
set `--max-files`, `--max-bytes`, `--max-matches` when needed. Hidden paths are
opt-in; dependency/build directories are excluded by default. Targets are parsed
without importing them. No `.gitignore` interpretation is implied.

## Observed trial

On 2026-10-21 at `2192ff22acafdd09e80ac722df91ee937aaedd5e`, the first command parsed
4 files and found 12 definitions with no errors, skips or
truncation. Examples:

- `vendor_sync.py`: `collect_lock_pins`
- `vendor_sync.py`: `collect_workflow_pins`
- `vendor_sync.py`: `validate`
- `vendor_sync.py`: `validate_child_heads`

The second command selected 24 YAML/TOML files at the same revision.
These are observations for that snapshot, not future expected counts. Full
query options, source SHA-256 values and results are recorded in xprobe's
[`docs/evidence/convention-discovery.json`](https://github.com/myon-bioinformatics/xprobe/blob/045b54dd35baf43758a484e458712ed5ad47ba24/docs/evidence/convention-discovery.json).

This is an explicit developer checkout, separate from existing vendored copies.
Existing vendor locks and CI/deployment configuration are unchanged; promoting
these APIs to an installed consumer requires its normal source-pin review.
