# Vendor update triage — 2026-10-19

## Decision

Update only the parent's locked `gh_identity.py` and use it for duplicate-file
collection. Keep every child pin and the advisory catalog unchanged. No
`gh_ops.py` or `xprobe.py` redistribution is justified by the observed consumers.

Source: [scheduled run 39184230142](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/39184230142),
[artifact 12157820356](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/39184230142/artifacts/12157820356).
The retrieved JSON reports 23 repositories, 266 groups, 647 copies, 30 stale
copies, 241 warning groups, `complete=true`, and no collection errors.
Every reported stale target copy matches its recorded lock blob. Staleness is
an upstream content difference, not evidence of corruption or a required update.

The [comparison evidence](evidence/vendor-triage-20261019.json) records each
consumer's inspected main HEAD, exact report pin/blob, changed existing upstream
functions, and recent CI runs with their actual SHAs. It describes the baseline
before this PR. Checks at older SHAs and checks selected by path filters are
not assertions that all workflows ran at the current head.

## gh_identity.py

Comparison upstream: `ce69e047735bc5248383037a4920bdb6130ccaf9` (main, CI success).
Destination paths below are relative to each consumer.

| Consumer | Existing source commit | Destination | Actual use and decision |
| --- | --- | --- | --- |
| parent | `8a04a246484b3d73e1e2d41d70b14ac4e476bdf3` | `vendor/gh_identity.py` | Git/CI reads, source identity and vendor audit; update to the collector's formal merge commit and reuse it |
| Ironmate | `8a04a246484b3d73e1e2d41d70b14ac4e476bdf3` | `vendor/gh_identity.py` | Adjacent `gh_ops` GitHub reads; preserve pin, no current caller of changed collector/subtree APIs |
| ascii_artist | `c6b52a29d8cd1dce0ebe0c4288cd16b0e4b84b96` | `vendor/gh_identity.py` | Repository metadata/diagnostics; preserve existing v1 contract |
| markdown | `c6b52a29d8cd1dce0ebe0c4288cd16b0e4b84b96` | `vendor/gh_identity.py` | Repository metadata/diagnostics; preserve existing v1 contract |
| mcp-toolcall-lab | `c6b52a29d8cd1dce0ebe0c4288cd16b0e4b84b96` | `vendor/gh_identity.py` | Repository diagnostics and adjacent `gh_ops`; no demonstrated benefit from collector/subtree changes |
| browser-test-kit | `c6b52a29d8cd1dce0ebe0c4288cd16b0e4b84b96` | `scripts/gh_identity.py` | `repo_overview.py` calls Git inventory/history; preserve working contract |
| web-ui | `c6b52a29d8cd1dce0ebe0c4288cd16b0e4b84b96` | `tool/vendor/gh_identity.py` | `repo-metadata --format v1`; preserve working contract |
| Err-o-RoadRunner | `c6b52a29d8cd1dce0ebe0c4288cd16b0e4b84b96` | `vendor/gh_identity.py` | Shared toolkit/adjacent `gh_ops`; preserve pin |
| Flutter Navigation Basic | `68ba77ede5456e37ac50964c91003d64573c2983` | `python/vendor/gh_identity.py` | Git inspection/`gh_ops` search; preserve nested lock and approved Non-Dart CI digest |
| yourself | `fc2c527257b12eb99c00637bbae74f8988fd6bf4` | `vendor/gh_identity.py` | Enrollment/import smoke; no production consumer of new APIs found |
| nvd_nist_known_vulns | `fc2c527257b12eb99c00637bbae74f8988fd6bf4` | `vendor/gh_identity.py` | Enrollment/import smoke; no production consumer of new APIs found |

The newer upstream adds fleet/PR/check/merge tools, public GitHub helpers,
subtree safety fixes, and duplicate collector fixes. Existing changed functions
are recorded separately from additions in the evidence. Metadata producers and
JUnit consumers do not acquire those new features just by copying the file.
The old `fc2c527` copies have a much wider transport/API delta, so updating an
unused enrollment would add validation work without a demonstrated user benefit.
The other retained pins are deferred based on usage; this investigation does not
claim that each was intentionally chosen to avoid a specific upstream bug.

The parent currently downloads collector commit
`a85022b88ca808dec7ef497ead1e3469906010a4` independently of its lock. Its
`gh_identity.py` bytes are identical to formal merge commit
`883a821400459a20f3589521bef64b22bb340c32` (upstream PR #82), which is on main's
first-parent history and has successful upstream CI. Adopt that smaller boundary
instead of unrelated later PR-check and Git-trial additions. Preserve the
upstream LICENSE bytes and update its provenance to the same source commit.

The scheduled job first verifies the existing `vendor-lock/1` hashes offline,
then passes `vendor/gh_identity.py` to the unchanged bounded report adapter.
Permissions, schedule, wall-clock timeout, incomplete-report behavior and artifact
retention remain unchanged. The actual locked collector is tested with combined
root/nested locks, a stale but matching copy, and a truncated tree. Existing
parent tests exercise `gh_ops`, Git inspection, source lookup and vendor policy.

## xprobe.py

Comparison upstream: `a43f0c2c81bd76f902408acecade921c587c0222` (main, CI success).
All six locked copies have blob `8cc1abbaf4269e5298de44f1b4ce7de692ec9ae2`.

| Consumer | Existing source commit | Destination | Decision |
| --- | --- | --- | --- |
| yourself | `37d582ee4039d2803335b54e855ed770caedabfc` | `vendor/xprobe.py` | Keep pytest/JUnit observation baseline |
| Flutter Navigation Basic | `37d582ee4039d2803335b54e855ed770caedabfc` | `python/vendor/xprobe.py` | Keep failure-identity/JUnit bridge baseline |
| Err-o-RoadRunner | `37d582ee4039d2803335b54e855ed770caedabfc` | `vendor/xprobe.py` | Keep shared toolkit baseline |
| Ironmate | `642999cea4185a68bffa7f7ccc46bd78dde03e5a` | `vendor/xprobe.py` | Keep adopted toolkit baseline; CI JUnit importer is separately pinned |
| browser-test-kit | `642999cea4185a68bffa7f7ccc46bd78dde03e5a` | `tests/vendor/xprobe/xprobe.py` | Keep JUnit evidence baseline |
| search_seq_including_spaces | `642999cea4185a68bffa7f7ccc46bd78dde03e5a` | `vendor/xprobe.py` | Keep pytest receipt baseline |

AST comparison (ignoring source locations) confirms `cases_from_junit`,
`pytest_receipt`, `corpus_to_json`, `corpus_from_json`, and `merge_cases` are
unchanged between all six copies and current upstream. New functions cover
normalization/search presets, factor generation, explanations, config loading,
annotations, portability, emoji and hidden text. `generate_cases` and search
internals have changed, so these additions are not assumed behavior-neutral.
Existing callers do not require them. Keep the deliberately selected catalog
JUnit bridge revision; no catalog promotion, adapter repin, or new dependency.

## CI, exclusions and integration order

Normal main CI is successful for the inspected consumers (see exact run/commit
records). Err-o-RoadRunner's scheduled Harvest failure corpus run
`39154504187` failed while normal CI `39138452565` succeeded at the same main
SHA; no causal relationship to these vendor differences was established.
Copilot review execution failure is not independent review approval.

`gh_ops.py` already has identical bytes across six repositories, including its
origin, with no stale pins. Do not redistribute it or factor out its subtree
helpers as part of this change. The archived legacy CUI consumer is excluded
from the active report and is not migrated. Existing `xprobe_pytest.py` and
workflow-only JUnit importer pins are separate artifacts and remain unchanged.

Only one parent PR is needed; no child PRs, retargeting, child-head baseline
changes, or catalog recommendation changes. Require the final head's existing
CI plus independent review before merging. Any later rollout must first name a
consumer need and validate its own lock, LICENSE, paths, supported Python/OS
matrix and parent baseline; this audit does not authorize blind latest updates.
