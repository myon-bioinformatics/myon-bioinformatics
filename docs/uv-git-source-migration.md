# uv for every Python tool

The end state is uv management for every Python tool across these repositories,
including stdlib-only single-file tools. A standalone copy is not a reason to
leave a tool off uv. When someone wants the file itself, copy the file from
the GitHub repo (the `repository` and `source` path in `vendor-catalog.json`).

This is a parent-only migration pilot. It now makes the parent's `py_launch.cjs`
installable as a minimal npm Git package, but does not yet edit child
repositories, `vendor.lock.json`, or `templates/vendor-child-ci.workflow.yml`.
A green parent check does not block a child merge and is not reviewer approval.
`child-heads.json` entries are pinned revision records, not packages.

## Environments, not a permanent exclusion

`pilot/vendor_dispatch.py` reads `vendor-catalog.json` only. It skips with a
reason when the current environment cannot run uv: `python -S`, Python older
than 3.11, uv missing, or an offline run. Node older than 24, or Node missing,
skips Node entries the same way. Those skips are the environment. They do not
classify the tool as unable to move.

Where the git tree is not an installable project yet, the driver also skips,
and names that gap (`package = false`, no project file, or third-party
dependencies). `uv pip install` of a `package = false` tree still attempts a
build and fails. Adding the project file is later work in that tool's own
repository. This pull request does not add those files.

## Classification

**Now.** uv git install of the repository root works today. The source path is
a file inside that project, not a subdirectory fragment.

| Repository | Source |
| --- | --- |
| browser-test-kit | `scripts/check_evidence.py` |
| browser-test-kit | `scripts/check_png.py` |
| browser-test-kit | `scripts/jsonl_digest.py` |
| browser-test-kit | `scripts/image_diff.py` |

**Move later.** uv is the end state. This pilot does not install them. Former
"cannot move" rows are in this class when the only blocker was the
standalone-copy contract (`python -S`, Python 3.9 as a property of the file,
or a verbatim enrolled copy). That contract is withdrawn as a uv exclusion.

| Repository | Source | Was |
| --- | --- | --- |
| gh_identity | `gh_identity.py` | cannot move (standalone copy) |
| myon-bioinformatics | `ci_status.py` | cannot move (standalone copy) |
| myon-bioinformatics | `vendor_sync.py` | cannot move (standalone copy) |
| myon-bioinformatics | `vendor_catalog.py` | cannot move (standalone copy) |
| myon-bioinformatics | `vendor_stage.py` | cannot move (standalone copy) |
| Ironmate | `pillow_min.py` | cannot move (standalone copy) |
| myon-bioinformatics | `gh_ops.py` | package first |
| myon-bioinformatics | `gh_workflow.py` | package first |
| myon-bioinformatics | `node_launch.py` | package first |
| myon-bioinformatics | `git_inspector.py` | package first |
| myon-bioinformatics | `browser_evidence.py` | package first |
| myon-bioinformatics | `publish_check.py` | package first |
| myon-bioinformatics | `py_matrix.py` | package first |
| myon-bioinformatics | `pytest_brief.py` | package first |
| ascii_artist | `ascii_artist.py` | package first |
| cli_args | `cli_args.py` | package first |
| markdown | `markdown.py` | package first |
| nvd_nist_known_vulns | `nvd_nist_known_vulns.py` | package first |
| xprobe | `xprobe.py` | package first |
| xprobe | `scripts/xprobe_pytest.py` | package first |
| yourself | `yourself.py` | package first |
| web-ui | `web_ui.py` | package first |
| BlueProbe | `blueprobe.py` | package first |
| QueRyu | `queryu.py` | package first |
| PythDRagoras | `pythdragoras.py` | package first |
| convert_img_fmt_to_webp-CUI- | `convert_img_fmt_to_webp(CUI).py` | package first |
| image_ext_converter_gui | `image_ext_converter_gui.py` | package first |
| video2audio_gui | `video2audio_gui.py` | package first |
| audio_any2any_gui | `audio_any2any_gui.py` | package first |
| video_to_image_gui | `video_to_image_gui.py` | package first |
| mcp-toolcall-lab | `src/mcp_toolcall_lab/github_public_resolver.py` | installable project; third-party dependencies, later |
| mcp-toolcall-lab | `src/mcp_toolcall_lab/adapters/html_snapshot.py` | installable project; third-party dependencies, later |
| LINENotifyAPI_Template | `LINENotifyAPI_Template.py` | single-file Python; catalog skip stays `archived_repository` |
| niconicoSearchAPI_Template | `niconico_search_api_template.py` | single-file Python; catalog skip stays `archived_repository` |
| convert_img_fmt_to_webp-GUI- | `convert_img_fmt_to_webp(GUI).py` | single-file Python; catalog skip stays `archived_repository` |
| scapy_GUI_for_Beginners | `scapy_gui.py` | single-file Python; catalog skip stays `archived_repository` |
| GitHub_api_Template | `github_api_template.py` | single-file Python; catalog skip stays `archived_repository` |
| python3nmap_GUI_for_Beginners | `python3nmap_gui.py` | single-file Python; catalog skip stays `archived_repository` |
| Qiita_API_Template | `qiita_api_template.py` | single-file Python; catalog skip stays `archived_repository` |
| HelpYouBuildServer | `main.py` | single-file Python |
| cumlative_usage | `cumlative_usage.py` | single-file Python |
| oci-cost-analysis | `oci-cost-analysis.py` | single-file Python |

Catalog `skipped` enrollment is unchanged. It still governs recommendations. It
does not mean the file cannot become a uv project later.

**Outside uv.** These are not Python modules. uv does not install them. The
standalone-copy contract was not their blocker. Copy the file from the GitHub
repo when the bytes themselves are needed. The driver uses npm for Node rows that the repository explicitly exports
and packs, and retains raw fetch for other sources. A `private: true`
package **can** be installed from Git; it only prevents registry publication.
The parent `py_launch.cjs` is npm-installable after the package change lands
on the selected Git commit. The browser-test-kit source files listed below
are not exported by that package, so they remain raw-fetch candidates.

| Repository | Source |
| --- | --- |
| myon-bioinformatics | `py_launch.cjs` (npm Git package; see `docs/npm-git-source-migration.md`) |
| browser-test-kit | `scripts/interaction.mjs` |
| browser-test-kit | `scripts/browser_execution.mjs` |
| gh_identity, Ironmate, and this parent | `LICENSE` next to a locked tool |
| web-ui | `css/*.css`, `js/*.js`, `js/flow.cjs`, `js/flow-patch.cjs`, `examples/pr-flow.json` |

## What this parent runs

CI runs the driver with `--resolve-ref main --record`. For **Now** Python rows it
uses `uv pip install` of the git URL; for explicitly exported Node rows it
uses an npm Git dependency pinned to the resolved full commit SHA. There is no consumer project file and no
committed `uv.lock`. The resolved commit is written into that tool's `commit`
in the job workspace. Reviewed catalog pins in git are not moved here.

`vendor-catalog/1` cannot store a branch beside the commit, and it has no
digest field. The command flag is the dynamic selector. Fetch digests are
printed. Enrolled digests stay in `vendor.lock.json` until a later reviewed
baseline, checked by `vendor_sync.py` as today.

## How a child would switch

Not done in this pull request. A child keeps its current lock until it chooses
uv. Tools that already have an installable project can be installed from the
git URL. Single-file Python tools move when their own repository gains a
project file; until then, copy the file from the GitHub repo if a standalone
copy is what the child wants. This pull request does not retarget a child lock
or the child workflow template.

## Child lock dispatch

`pilot/vendor_dispatch.py --manifest vendor.lock.json` also reads a
`vendor-lock/1` file. Each row goes to uv or Node the same way, or is skipped
with a reason. Every reachable row is also fetched and its `blob_sha` and
`sha256` are checked against the recorded commit. `--record` writes `ref`,
`commit`, `blob_sha`, and `sha256` back into that same lock. No `uv.lock` and no
second manifest are written. A mismatch at the recorded commit is an error.
