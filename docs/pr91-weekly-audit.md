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

`xprobe` was included despite being absent from `child-heads.json`. The original
version made these findings warnings in PRs but enforced them after a main push
or on the daily parent-child schedule. That was the reported Blocking: merging
would activate a known-failing default gate. The revised #91 separates
**automatic gate activation** from the reporting/tool implementation. It does
not add the new main-push fleet trigger, and the fleet job only runs when
explicitly dispatched with `strict-pin-readiness=true`. In that mode it remains
strict (`--enforce --require-complete`), preserves its failed JSON/log and cannot
run autofix. Existing scheduled vendor-baseline validation remains unchanged.
The duplicate-files job remains independent.

The 16 findings have **not** been resolved by this staging change. Automatic
fleet enforcement and uniform parent-owned child rollout are tracked in parent
[#56](https://github.com/myon-bioinformatics/myon-bioinformatics/issues/56).
See [parent-pin-fleet.md](parent-pin-fleet.md) for activation conditions,
including the update window needed when parent main advances. A green ordinary
PR/main/schedule workflow is not a clean-fleet or rollout approval.

Before release, verify:

1. Same-head CI, including actionlint and the timeout/evidence regressions.
2. A `workflow_dispatch` run of the exact #91 branch/head, for example:
   `gh workflow run vendor-lock-reconciliation.yml --ref integrate/open-prs-20261016`.
3. The run actually includes `duplicate-files`; inspect its exit status,
   summary, artifact and raw `complete`/`errors`/`scanned` fields. Match the
   scanned set to the eligible public inventory, and inspect reconciliation
   separately. Green CI alone is insufficient.
4. Confirm the staged boundary: the known pin failures remain visible and
   strict manual readiness stays red until resolved; automated fleet gate
   activation is deferred, with acceptance conditions in parent #56. Preserve original source
   histories with a merge commit if integrating this multi-parent PR; do not
   replace the final upstream merge SHA with an intermediate feature SHA.

Full collection does not establish full vendor provenance. This pinned
collector recognizes `vendor.lock.json` and `tool/python/vendor.lock.json`;
for example Flutter's relocated `python/vendor.lock.json` is not read by this
version. Such files can be counted with null provenance. The report is not
a replacement for the parent's independent registered-lock/source-byte audit.

The 16-checkout patch application, mandatory child merge gates, and automatic
rollout are not exercised by this weekly report validation.
