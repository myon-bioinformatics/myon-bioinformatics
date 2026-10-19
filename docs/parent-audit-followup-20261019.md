# Parent audit follow-up (2026-10-19)

## Evidence and scope

These observations were collected against parent main `c34d239e20b97a9d81914e241b025eb832245661`. They justify two bounded parent repairs, not a fleet-wide baseline upgrade.

- [Parent pin readiness audit](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/39243067175): the complete read-only audit covered 22 public child repositories (16 registered and 6 discovered) with no audit errors. Twenty had stale uniform parent pins and two had mixed parent pins. Freshness is not compatibility evidence; adopted functional baselines still require deliberate review.
- [Lock reconciliation](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/39243839336): the existing Flutter and MCP lock topology matched the parent registry. Each had four catalog-baseline differences; five and seven unregistered candidates, respectively, lacked adequate fresh evidence. Compatibility remains unverified. No topology rewrite or catalog promotion follows from this report.
- The accompanying [duplicate-file audit](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/39243839336/job/118030832632) completed across 23 public repositories without errors. Its 33 upstream-different locked copies matched their own recorded locks. Upstream difference alone does not prove corruption or authorize replacing an adopted copy.

## Historical pin evidence is not an active dependency

The [failing parent-pin job](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/39242331443/job/118025887304) counted nine observations, spanning three historical SHAs, inside `docs/evidence/vendor-triage-20261019.json` as current parent pins.

The enforced parent check now excludes that exact evidence file in its caller workflow. Its contents and historical SHAs are unchanged. No general `docs/**` or `docs/evidence/**` exclusion is introduced, and the scanner's reusable defaults are unchanged. Regressions load the actual caller exclusions and prove that an adjacent runtime JSON and an active workflow remain detected and fail inconsistent-pin enforcement.

## Disposable Git repositories must not launch background maintenance

The [scheduled validation job](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/39216192540/job/117940494283) and [autofix job](https://github.com/myon-bioinformatics/myon-bioinformatics/actions/runs/39216192540/job/117941264784) both failed while deleting a temporary branch-scan repository's `.git/objects`, reporting `ENOTEMPTY`.

Git documents that fetch can run automatic maintenance and that maintenance may detach. A local real-Git file-remote regression observed two maintenance subprocess launches in the old branch-scan implementation. A background writer racing temporary-directory cleanup is therefore a plausible cause, but the production `ENOTEMPTY` race itself has not been reproduced deterministically.

The temporary scan's Git command prefix now sets `maintenance.auto=false` and `gc.auto=0`. This is confined to the disposable repository's subprocesses; it changes no global, source-repository, or user configuration. The regression performs actual fetches, verifies unchanged branch findings and completed directory cleanup, and uses Git trace events to prove no maintenance/GC workers launch. A separate regression confirms cleanup errors still propagate; errors are not hidden or converted into success.

Primary behavior references: [git-fetch automatic maintenance](https://git-scm.com/docs/git-fetch), [Git maintenance and GC configuration](https://git-scm.com/docs/git-config).

## Verification boundary

The focused pin/branch suites passed 115 tests with one existing authenticated GitHub test skipped. The same new regression tests failed on the old implementation (three failures), then passed after these changes. Workflow actionlint and the exact-exclusion offline enforced pin scan passed. This establishes the prevention mechanism and the historical-evidence fix, not a claim that every possible filesystem cleanup race is solved.

No child locks, child reusable-workflow pins, registry topology, catalog recommendations, or baseline SHAs are updated by these two repairs. Parent CI continues to audit recorded revisions; this does not create a required check on every child pull request.
