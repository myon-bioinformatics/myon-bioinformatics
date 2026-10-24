# Cross-repository use cases and verification status

Records the roles of the 23 public, non-archived repositories observed on 2026-10-21.
Following the 3 in the previous wave, the remaining 20 were also advanced to implementation or offline verification using existing inputs and outputs.
This does not mean every command, live service, or automatic connection is complete. Manual bridging and unconfirmed scope are recorded too.

Machine-readable observations are in [cross-repository-use-cases.json](cross-repository-use-cases.json),
and the tests and connection evidence for reproducing them are in the [verification record](cross-repository-verification-20261021.md).
README blobs are observations from the previous wave, not a guarantee of the latest version. Those checked against the current main are recorded in the JSON's `observed_head`.

## Use cases and connections

Working directory, dependencies, and input conditions follow each README. `<...>` stands for user input.
Running DRAgoWing as a module assumes the parent-directory layout described in its README.

| Repository | When to use | Entry point | Connection and scope |
| --- | --- | --- | --- |
| [Aoi](https://github.com/myon-bioinformatics/Aoi/blob/main/README.md) | Select child projects' Python sources and survey how dependencies are used | `python -S scripts/survey_third_party.py --match 'blueprobe/*' --list` | Keeps dependency declarations and local names from all roots, and passes the selected sources to the existing survey |
| [ascii_artist](https://github.com/myon-bioinformatics/ascii_artist/blob/main/README.md) | Draw processing relationships in a terminal or text report | `diagram(...) / render_diagram(...)` | Formats a selected execution plan into a lightweight text diagram |
| [BlueProbe](https://github.com/myon-bioinformatics/BlueProbe/blob/main/README.md) | Record structure and unextracted parts from fetched HTML | `blueprobe.py / HtmlSource / inspect_queryu_cache` | Takes QueRyu's selected cache as observation input |
| [browser-test-kit](https://github.com/myon-bioinformatics/browser-test-kit/blob/main/README.md) | Carry page/environment target selection through comparison and summary | `scripts/app_comparison.py / scripts/app_digest.py --list` | Calls Ironmate's selection API and does not decode out-of-scope PNGs |
| [cli_args](https://github.com/myon-bioinformatics/cli_args/blob/main/README.md) | Share argument validation and dry-runs of planned execution | `python -S examples/run_cli.py --format json --dry-run -- git status --short` | Passes selected targets explicitly to existing commands |
| [DRAgoWing](https://github.com/myon-bioinformatics/DRAgoWing/blob/main/README.md) | Select saved results by ID, column, and condition and display them | `python -m dragowing --results <DIR> --out <OUT> --id <ID>` | Passes selected results to the existing HTML view and its own Markdown report. An additional web-ui adapter is unverified |
| [Err-o-RoadRunner](https://github.com/myon-bioinformatics/Err-o-RoadRunner/blob/main/README.md) | Collect stderr, exit codes, and JUnit only on failure | `python3 -S triage.py digest <artifacts.zip> --limit 5` | Passes selected failure evidence together with ghi run/job identifiers |
| [flutter_navigation_basic](https://github.com/myon-bioinformatics/flutter_navigation_basic/blob/main/README.md) | Extract screen responsibilities, reachability, and unclassified states | `python -S python/pattern_responsibility_audit.py --pattern '00?' --json --check` | Narrows the ID/state/responsibility display after auditing all 198 items. Keeps overall errors and the check result |
| [gh_identity](https://github.com/myon-bioinformatics/gh_identity/blob/main/README.md) | Check the checkout, Git/gh availability, and target PR/SHA/CI | `gh_identity.capabilities() / git_status() via parent preflight.py` | Uses the parent's adopted ghi to observe Git/gh/Python/token presence and worktree state |
| [Ironmate](https://github.com/myon-bioinformatics/Ironmate/blob/main/README.md) | Select pages and environments and generate image comparison sheets | `python -S browser_matrix.py <ROOT> --page 'flutter-*' --env 'Linux-*' --list` | Receives the same selection from browser-test-kit's comparison and summary |
| [markdown](https://github.com/myon-bioinformatics/markdown/blob/main/README.md) | Round-trip convert and diff only the needed conversion cases | `scripts/converter_parity_report.py / scripts/converter_contract_audit.py --list` | Selects by typed case key and records the partial-evaluation scope in the report |
| [mcp-toolcall-lab](https://github.com/myon-bioinformatics/mcp-toolcall-lab/blob/main/README.md) | Select offline fixtures by name and replay them | `python -m mcp_toolcall_lab.prompt_experiment replay --list` | Uses a shared selection for both replays; listing does no loading, replay, or output |
| [myon-bioinformatics](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/main/README.md) | Keep checking use cases, entry points, connections, and verification status | `python -S preflight.py --root . --require git --python-min 3.11` | Adds yourself's OS/Python JSON to the adopted ghi prerequisite check, and records use cases, implementation, and verification in this list |
| [myon-bioinformatics.github.io](https://github.com/myon-bioinformatics/myon-bioinformatics.github.io/blob/main/README.md) | Import verified lab public exports into tool links with publication IDs | `python -S scripts/import_lab_pages.py --source <EXPORT>` | Browsed from the existing Tools & Demos. Listing in project search needs manual curation of projects.json |
| [NagoyAction](https://github.com/myon-bioinformatics/NagoyAction/blob/main/README.md) | Check defined processing with doctor/show before running | `python nagoyaction.py doctor <pipeline.toml>` | Checks ghi/yourself environment observations first, then separately detects pipeline-specific gaps with doctor |
| [nvd_nist_known_vulns](https://github.com/myon-bioinformatics/nvd_nist_known_vulns/blob/main/README.md) | Fetch CVE information for an explicit CPE and version | `python nvd_nist_known_vulns.py --cpe '<CPE23>'` | Uses the entry point that matches by explicit CPE. The caller provides the package→CPE mapping; CVEs are never concluded from a package name alone |
| [PythDRagoras](https://github.com/myon-bioinformatics/PythDRagoras/blob/main/README.md) | Compare condition candidates and keep counterexamples | `pythdragoras.py / littlebit_approx.py` | Evaluates condition candidates from selected result data |
| [QueRyu](https://github.com/myon-bioinformatics/QueRyu/blob/main/README.md) | Select caches by group/key/time and share them | `python cache_share.py --cache <CACHE> query --group <GROUP>` | Passes the selected cache to BlueProbe without refetching |
| [SakAnalytics](https://github.com/myon-bioinformatics/SakAnalytics/blob/main/README.md) | Build metrics from observation JSONL and a config | `python sakanalytics.py --games <JSONL> --config <TOML> --out <OUT>` | Aggregates selected observations according to a generic config |
| [search_seq_including_spaces](https://github.com/myon-bioinformatics/search_seq_including_spaces/blob/main/README.md) | Select sequence candidates by format, compression extension, and name | `python -S sequence_tool.py discover <ROOT> --format fasta --pattern 'sample*'` | Passes candidate JSON to the existing read, validate, and analyze commands |
| [web-ui](https://github.com/myon-bioinformatics/web-ui/blob/main/README.md) | Reuse shared UI and HTML generation for report screens | `from web_ui import render_document` | Displays selected tables, graphs, and reports in a single HTML page |
| [xprobe](https://github.com/myon-bioinformatics/xprobe/blob/main/README.md) | Find files, Python symbols, and settings by rules | `python -S xprobe.py --symbols <ROOT> --symbol-pattern '*Adapter' --brief` | Discovers candidates statically, distinct from runtime registration or full proof of behavior |
| [yourself](https://github.com/myon-bioinformatics/yourself/blob/main/README.md) | Check observed OS, Python, and runner values | `python -S yourself.py --format json` | Passes collect()'s OS/runtime JSON to the parent preflight, discarding host and directory |

## Implementations added this time

- [Aoi #110](https://github.com/myon-bioinformatics/Aoi/pull/110): adds `--match`, `--exclude`, and `--list` to the existing dependency survey. Local modules outside the selection are still classified as local, and dependency declarations reference all roots. 8 tests pass. Listed 34 blueprobe paths with `python -S`. The scope of other inventories and shrink generation is unchanged.
- [Flutter #217](https://github.com/myon-bioinformatics/flutter_navigation_basic/pull/217): adds `--pattern`, `--status`, and `--owner` to the existing audit. All 198 items are still audited and errors/receipt are kept; only the display is narrowed. 34 tests pass, and the output and exit values of 4 default CLI cases match the pre-change ones. `--check` still fails when an excluded ID is missing.
- [Parent #131](https://github.com/myon-bioinformatics/myon-bioinformatics/pull/131): a [prerequisite check](preflight.md) connecting the adopted ghi and yourself JSON. 3 related tests, a real `python -S` connection, and the existing stdlib smoke pass. The fix rejecting deep JSON with exit 2 was also independently rechecked. The parent's full test suite: 1294 passed, 64 skipped, 50 subtests passed.

Each implementation was independently reviewed by a separate agent. Feature CI and the final head were checked, and all 3 were merged to main.
A Copilot automatic review that hit a service error is not treated as a successful review. Vendor locks, registrations, and workflow permissions are unchanged.

## Use cases confirmed with existing entry points

QueRyu→BlueProbe→SakAnalytics, PythDRagoras's saved-result comparison, and DRAgoWing's own Markdown report ran with existing inputs and outputs.
These 5 pass 190 tests. ascii_artist's text diagrams, cli_args's explicit argv, failure digests, static symbol search, sequence candidates through analysis, and web-ui's HTML generation were also confirmed.
Details, pinned heads, test selections, input provenance, and limits are in the verification record and JSON.

The portfolio import works as tool links with publication IDs. Project search cards rely on manual curation of `projects.json`; automatic registration from the importer is not implemented.
For NVD, explicit CPEs and offline basic processing were confirmed; automatic CPE inference from package names and real CVE applicability decisions are not done.

## Previous wave merges

| Implementation | Merge commit on main | Feature CI passed | Default skips |
| --- | --- | --- | --- |
| browser-test-kit #110 | [b353700](https://github.com/myon-bioinformatics/browser-test-kit/commit/b35370057de2d21e0306629787ad3d6668847fee) | 23 | 0 |
| mcp-toolcall-lab #148 | [8a60c11](https://github.com/myon-bioinformatics/mcp-toolcall-lab/commit/8a60c116237ede6deeeafbf4bea16e5b3f8845ee) | 8 | 0 |
| markdown #117 | [0c818f1](https://github.com/myon-bioinformatics/markdown/commit/0c818f116659adebaa2d4042d3b356e5f3094f14) | 24 | 2 |

The previous wave checked 64, 57, and 25 related tests respectively, plus the post-merge tree and real commands. The connection to Ironmate's comparison and summary was completed in browser-test-kit #110, so the same implementation is not added again.

## Merge record for this time

| Implementation | Merge commit on main | Feature CI passed | Default skips |
| --- | --- | --- | --- |
| Aoi #110 | [cadc40a](https://github.com/myon-bioinformatics/Aoi/commit/cadc40a4a52eb0165fb98543a089fcfaab5f3359) | 18 | 1 |
| Flutter #217 | [eed4780](https://github.com/myon-bioinformatics/flutter_navigation_basic/commit/eed47808c22282adb54a20b788c64dab4cfa2022) | 13 | 13 |
| Parent preflight #131 | [611e127](https://github.com/myon-bioinformatics/myon-bioinformatics/commit/611e127b38001be4aab5425e259575a8420d2ab6) | 15 | 1 |

The merged trees match the trees of the independently reviewed heads. After merging, the related tests (Aoi 8, Flutter 34, preflight 3) and real commands were rechecked. For Aoi, the listing of 34 paths matched the survey selection count, and the existing full evidence check passed. Flutter kept all 198 items with 0 errors even when displaying 2. preflight accepted real yourself JSON, met the Git/Python requirements, and showed the supplied receipt as unverified.

After #131, parent main received an automatic update touching only 2 statistics image files, [94218fa](https://github.com/myon-bioinformatics/myon-bioinformatics/commit/94218fa5c4f0f2585fbd306435545a00ba5766f8). This verification record is based on the tree after that update and keeps the image update. For Aoi and Flutter, main and unresolved review threads were also rechecked right before merging.
