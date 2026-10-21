# Remaining repository reuse verification, 2026-10-21

This supplements [the 23-role inventory](cross-repository-use-cases.json). Two
separate read-only reviewer agents checked existing connections; implementations
were made separately and independently reviewed. Tests are focused selections,
not a claim that every repository command or live service was exercised.

## Source and fixture boundary

`observed_head` in the inventory identifies the public main revision checked.
For the older local checkouts, the product files actually used were compared
with immutable remote blobs; the entire checkout is not claimed to match main.
BlueProbe, QueRyu, SakAnalytics, PythDRagoras and DRAgoWing differed from their
local checkouts only in workflows and workflow-pin tests. Their eight relevant
product files were also compared individually. Six other repositories had their
main entry point and README blobs compared; xprobe used its current main source.

The analysis inputs came from Aoi commit
`f1329621f91e4a83a330041a8ec9d00526810f18`, not an assertion about latest Aoi
main data. DRAgoWing used the actual custom Markdown implementation (source blob
`e6dc3771365cae701bd079f104fedcb4f3408647`, not a PyPI substitute); the existing tests additionally cover
native versus stdlib output. MCP integration used the reviewed source merged in
[mcp-toolcall-lab #148](https://github.com/myon-bioinformatics/mcp-toolcall-lab/pull/148).
No live provider, NVD query, deployment or new data acquisition was performed.

## Existing tests rerun

Run each command inside the named repository. `PY` is the development Python
with that repository's declared test dependencies. `AOI_ROOT`, `LAB` and sibling
paths are explicit clean fixture/source checkouts, not mandatory dependencies of
the standalone selection CLIs. Optional integration dependencies were installed
for these test selections; these are distinct from the `python -S` smoke runs.

| Repository | Test selection (`$PY -m pytest -q …`) | Result |
| --- | --- | --- |
| BlueProbe | `tests/test_blueprobe_core.py tests/test_blueprobe_snapshot_source.py` | 21 passed |
| BlueProbe | `--require-blueprobe-lab-integration` with the three named integration tests below | 3 passed |
| QueRyu | `tests/test_cache_snapshots.py tests/test_queryu_cache_share.py` | 49 passed |
| SakAnalytics | `tests/test_sakanalytics.py` | 94 passed |
| PythDRagoras | `tests/test_littlebit_approx.py` | 15 passed |
| DRAgoWing | `tests/test_overlap_stdlib.py::test_c001_bytes_and_numeric_conditions tests/test_overlap_stdlib.py::test_cli_stdlib_writes_same_html tests/test_figures.py::test_cli_on_the_recorded_cycle_writes_standalone_html` | 3 passed |
| DRAgoWing | `tests/test_build_reports.py` | 5 passed |
| ascii_artist | `tests/test_ascii_artist.py` | 39 passed |
| cli_args | `tests/test_commands.py tests/test_stdlib_commands.py` | 17 passed, 1 skipped |
| Err-o-RoadRunner | `tests/test_artifact_digest.py` | 11 passed |
| nvd_nist_known_vulns | `tests/test_cpe_helpers.py tests/test_nvd_client.py` | 51 passed, 342 subtests passed |
| web-ui | `tests/test_python_wrapper.py` | 13 passed, 62 subtests passed |
| portfolio | `tests/test_lab_publication.py tests/test_portal_links.py` | 12 passed |
| xprobe | `tests/test_convention_discovery.py tests/test_python_symbols.py` | 30 passed |
| NagoyAction | `tests/test_nagoyaction.py -k doctor` | 3 passed, 13 deselected |

BlueProbe's three integrations were
`tests/test_blueprobe_html_source.py::test_optional_real_lab_extractor_reuses_all_calendar_rows`,
`tests/test_blueprobe_html_source.py::test_verified_cache_real_lab_snapshot_replay`,
and `tests/test_blueprobe_html_compare.py::test_real_saved_npb_fixture_coverage_and_css`.
Set `AOI_LAB_SOURCE_ROOT=$LAB` and `PYTHONPATH=$LAB:/path/to/QueRyu` for them.
QueRyu uses `AOI_ROOT` and `PYTHONPATH=/path/to/BlueProbe:/path/to/QueRyu/ask`;
SakAnalytics uses `AOI_ROOT` and `PYTHONPATH=$AOI_ROOT:/path/to/PythDRagoras`;
PythDRagoras uses `AOI_ROOT` and `PYTHONPATH=$AOI_ROOT:/path/to/SakAnalytics`;
DRAgoWing uses `AOI_ROOT` and
`PYTHONPATH=$AOI_ROOT:/path/to/PythDRagoras:/path/to/QueRyu/ask`.
These five repositories account for 190 successful tests with no skips.

search_seq additionally passed
`python -S -m unittest discover -s tests -p test_sequence_discovery.py` (6 tests).
Ironmate's consumer connection was already integrated in
[browser-test-kit #110](https://github.com/myon-bioinformatics/browser-test-kit/pull/110):
64 focused tests and actual stdlib comparison/digest were verified in the prior
wave; its remote merge was rechecked here, rather than counted as new test runs.

## Additional offline smokes

These were temporary invocations, separate from the test totals above:

- QueRyu query/snapshot → BlueProbe → SakAnalytics succeeded with sockets blocked:
  Chunichi RF4/RA3/W1/L0 and inverse opponent values. An excluded corrupt cache
  was not read; selecting it failed integrity validation. QueRyu → BlueProbe
  also produced one observation and unknown count zero under `python -S`.
- PythDRagoras's saved comparison was recalculated: 156 units, 17 existing and
  16 derived candidates; best MCC `0.8207826816681233` in both groups. The
  conclusion remains exploratory, not independent evidence of improvement.
- `reports/build_reports.py:build(Aoi_root, markdown_root, temporary_output)`
  produced 51 documents using real custom Markdown under `python -S`; provenance
  and saved renderer identity matched. A new DRAgoWing→web-ui adapter was not
  implemented or tested.
- ascii_artist rendered a selected three-step plan and preserved its diagram
  JSON roundtrip. web-ui rendered an authored table, escaped text diagram and
  inline SVG in one document. The caller must author/escape `trusted_html`.
- cli_args dry-run preserved `'selected file.fa'` as one argv item and returned
  `executed:false` for a child command that would otherwise exit 99.
- Err-o-RoadRunner digested a ZIP with one JUnit failure and an explicit receipt
  (`run_id`, `job_id`, `head_sha`); `--keys run_id,job_id,head_sha --limit 1`
  retained the identifiers and correctly returned exit 1. It does not obtain or
  verify the GitHub identities itself.
- NVD helpers parsed an explicit OpenSSL CPE and calculated CVSS 9.8; selected
  JSONL distinguished completed zero findings from missing measurements. No
  real CVE applicability claim or automatic package→CPE mapping was made.
- search_seq discover returned a spaced FASTA filename and gzip FASTA; each
  `ROOT / record['path']` was passed as explicit argv to `nucleotide-features
  FILE --alphabet dna --jsonl`, successfully. A JSON-to-argv caller loop is needed.
- xprobe found `OfflineAdapter` in a module with a top-level side effect without
  creating its marker. This proves static discovery, not runtime registration.
- yourself's actual `--format json` was accepted by parent preflight under
  `python -S`. The current yourself source blob was compared with main.
  Adopted ghi supplies the probes; its vendor pin was not changed. c001 doctor
  separately reported missing cache manifests and exit 1, not pipeline success.

## Remaining manual connections

Every reviewed repository has a concrete usable role. This does not mean all
bridges are automated. In particular, portfolio's allowlisted import and
publication identity are verified, but project search still follows curated
`projects.json`; the importer does not register a search card. SSOT curation
remains explicit. NVD still needs an explicit CPE mapping and a separate live
query/applicability review. Generic preflight does not replace NagoyAction doctor.
These limitations are also recorded per repository in the JSON inventory.

## New implementation CI and post-merge checks

Aoi #110 passed all five Python 3.11–3.15 jobs, plus the JUnit collector and
other functional checks (18 successes, one path-conditioned actionlint skip).
The Python 3.11 log reported 2174 passed, 41 skipped and 23 subtests passed.
Flutter #217 passed 13 functional checks with 13 path-conditioned skips; parent
preflight #131 passed 15 with one sync skip. Copilot returned a service error
and supplied no code review; separate reviewer agents reviewed the final code.
No GitHub rules were bypassed.

All three merged trees equal the reviewed head trees. Post-merge focused tests
passed again (Aoi 8, Flutter 34, preflight 3), alongside actual `python -S`
commands and Aoi's full committed-inventory check. See the inventory document
for merge commits, examples and the observed concurrent main update.
