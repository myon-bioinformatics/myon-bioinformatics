# PR #91: first weekly duplicate-files run

Recorded: 2026-10-17 JST. Reviewed source head:
`000541cfa5ef363566785e45bd0d56e09f3561b8` (tree
`cb336b9fa484ea4f93deb3ec88eb8d9792da49bc`). Parent main was
`8809306255d45650f43d85d1c42dda90fb4af02c`.

## Trigger and permission review

- `vendor-lock-reconciliation.yml` uses `30 0 * * 1`: Mondays at 00:30 UTC /
  09:30 JST. The next nominal occurrence is 2026-10-19. GitHub schedules run
  the default-branch workflow; queuing/delivery is not a punctuality guarantee.
- `duplicate-files` runs only for `schedule` and `workflow_dispatch`. PR/push
  exercise offline regressions and skip the live scan. There is no `needs`
  dependency: a reconciliation failure cannot suppress the duplicate scan.
- The workflow and scan inherit `contents: read`. All scan checkouts disable
  persisted credentials. Only the collection step receives `github.token` as
  `GH_TOKEN`; collected files are not executed, and the scan has no writes
  to repositories, issues or PRs. Only the reviewed pinned collector is executed.
  Artifact upload uses the Actions runtime
  service; extra repository write permissions are unnecessary.
- The existing workflow already has `workflow_dispatch` on main, so manual
  testing can select `integrate/open-prs-20261016` before merging. Verify the
  selected run's actual `head_sha`, event and jobs rather than inferring the
  new job ran from the workflow name alone.

## Collector identity and completeness

- The scan checks out and verifies the full commit
  `1a32ccce18e5278f2c4c0448a45f7f2c9d6c98f5` from
  `myon-bioinformatics/gh_identity`. This was that repository's main head at
  review and includes the merged #60 completeness contract and #69 transport
  fix. The reviewed `gh_identity.py` Git blob is
  `8f9889440ae6f438d8112621421201259ce67c01`.
- The collector enumerates the owner's public, non-archived repositories and
  excludes forks by default. It resolves each default-branch commit before
  reading its tree and lock. Truncated/invalid trees and per-repository read
  errors produce `complete=false`, `errors`, and CLI exit 1. Failure to list
  repositories can instead exit 2 with no JSON; the adapter still fails.
- The adapter requires a boolean `complete`, an `errors` array and consistent
  status. `complete=false`, malformed output, renderer errors and legacy pins
  cannot be presented as a successful complete fleet scan. A collector's
  positive exit code is preserved; other invalid results return 1.
- This parent keeps its existing adopted `vendor.lock.json` baseline. The
  separately checked-out reporting tool does not silently replace adopted
  vendor source pins or promote advisory catalog entries.

## Evidence and timeout correction

The parent adapter now imposes a 900-second subprocess wall-clock timeout in
addition to the collector's 900-second operation budget. Expiry returns 124,
keeps partial stdout, renders an INCOMPLETE summary and leaves time within the
20-minute job for upload. Nonpositive timeout arguments are rejected.

`duplicate-files` uploads these files with `if: always()` and 14-day retention:

- `reports/duplicate-files.json`: exact collector stdout, including malformed
  or partial output; it is not replaced with fabricated successful JSON.
- `reports/duplicate-files.md`: rendered report or explicit INCOMPLETE diagnosis;
  also appended to `GITHUB_STEP_SUMMARY`.
- `reports/duplicate-files.stderr.log`: collector diagnostics, including errors
  that happen before JSON can be emitted.

Upload is attempted after collection failure. A runner loss, cancellation,
job timeout before cleanup, or setup/checkout failure before collection can
prevent these files from being created/uploaded. An absent artifact is not
evidence of a complete audit.

## Merge and live-run gates

The original #91 head has six successful PR-triggered workflows. It has no
review submissions or inline threads of its own. Those green checks do not
mean its live scheduled scan has run or that parent main will remain green.

The same-head [fleet job](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/38905625407/job/116988340458)
actually reported:

```text
checked 17 public child repositories; 0 skipped; 16 pin finding(s);
0 audit error(s); 0 fetch warning(s)
```

`xprobe` was included despite being absent from `child-heads.json`. PR events
make these findings warnings. The unchanged policy enforces them for main
pushes and the daily parent-child schedule, so merging #91 with the observed
child state would fail that separate lane. The duplicate-files job remains
independent. Do not hide this by weakening enforcement or calling PR success
a successful live audit. Child rollout and baseline changes require the
existing parent-owned distribution/review process.

Before release, verify:

1. Same-head CI, including actionlint and the timeout/evidence regressions.
2. A `workflow_dispatch` run of the exact #91 branch/head, for example:
   `gh workflow run vendor-lock-reconciliation.yml --ref integrate/open-prs-20261016`.
3. The run actually includes `duplicate-files`; inspect its exit status,
   summary, artifact and raw `complete`/`errors`/`scanned` fields. Match the
   scanned set to the eligible public inventory, and inspect reconciliation
   separately. Green CI alone is insufficient.
4. Resolution or an explicit operational decision for the known strict
   parent-pin failures before main integration. Preserve original source
   histories with a merge commit if integrating this multi-parent PR; do not
   replace the final upstream merge SHA with an intermediate feature SHA.

Full collection does not establish full vendor provenance. This pinned
collector recognizes `vendor.lock.json` and `tool/python/vendor.lock.json`;
for example Flutter's relocated `python/vendor.lock.json` is not read by this
version. Such files can be counted with null provenance. The report is not
a replacement for the parent's independent registered-lock/source-byte audit.

The 16-checkout patch application, mandatory child merge gates, and automatic
rollout are not exercised by this weekly report validation.
