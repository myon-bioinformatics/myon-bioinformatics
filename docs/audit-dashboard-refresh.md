# Parent audit dashboard: freshness and publication

Scope: `myon-bioinformatics/myon-bioinformatics` PR #231, base head
`be7b48713143b9e208a3a921537d09504443c76b`. No portfolio changes.

## Observation and audit records

On startup, saved rows, completion records, history and weekly snapshots are loaded.
All PR CI observations initially display as unverified (previous value is shown),
so a stale failure does not create an automatic P0. Explicit manual priority remains.
Startup then searches all open items with pagination, adds missing items, and
refreshes observations. Header/weekly buttons repeat the full refresh; the detail
button refreshes one item. There is no background timer or unattended weekly run.

Only GitHub-owned fields are merged. Notes, reason, priority, state, next action,
completion date, dependencies, conflicts and user extension fields survive updates.
A successful observation appends before/after history; snapshots are immutable.
Closed/merged items are queried individually and retained; search absence never
means closed. GitHub closure never automatically completes an audit.

Checks are fetched for the PR's exact head SHA across all pages, along with all
commit statuses (latest status per context). The head is fetched again before
applying, rejecting a concurrent head change. No checks means unknown; cancelled,
skipped or neutral checks do not imply success. This is observed CI, not a claim
that branch-protection required checks have been satisfied. Review threads remain
unknown through public REST; follow the GitHub link to verify them.

403/429 stop subsequent requests until the retry/reset time (at least 60 seconds).
Communication, partial-page and head-change failures retain previous evidence and
show unverified status. Search failure does not reconcile the list. Successful
rows in a partial refresh remain usable. Full refresh visits oldest observations
first to allow progress across limited sessions. Public unauthenticated API limits
can prevent the entire fleet from refreshing; use detail refresh for urgent PRs.
No token is stored. The page must remain open for automatic startup refresh.

JSON export retains rows, snapshots and history. Import is an explicitly confirmed
replacement, as before, and imported CI is marked unverified. Export before import.
Invalid/unreadable local storage disables writes for that session so automatic
refresh cannot replace it with seed data. Storage quota failures are reported;
export the current in-memory data and free space before retrying in a new session.

## Live cross-check (2026-11-08)

- Aoi #167: head `d733e690c9f61f109bef377468e2b6201ad11643`; 22 returned
  check runs all successful. Replaced old head/failure evidence in seed.
- Parent open-PR list includes #230 (`e3cbbdfe0faf38e7b0d19d95d15f5ddf59800784`)
  and #231; both were absent from seed and are added with CI unknown.
- Seed now has 163 rows. This is a historical seed, not a claim that every row
  has been fully re-audited today. Every startup revalidates it.
- #231 head above has three successful workflow runs. This does not validate
  the new changes in this follow-up branch.

## Validation

Run from repository root:

```sh
node --check site/app.js
node --check site/sync.js
node --test tests/test_audit_sync.cjs
```

Tests cover audit field isolation, CI status aggregation, pagination, exact-SHA
requests, moving heads, discovery, completion/snapshot/history preservation,
429 fallback, filters, comparison, weekly and detail rendering in a simulated DOM.
No real browser visual verification was performed. For browser acceptance, serve
`site/` locally; export existing records, save a note and completion, refresh and
reload, compare old snapshots, test each filter/comparison, export/import JSON,
and simulate offline/403/429 and a multi-page response. Confirm no old failure
is counted as current and new open PRs appear even with existing browser storage.

## Pages conditions (configuration inspected; no deployment performed)

`audit-pages.yml` runs only on main pushes touching `site/**` or that workflow.
It calls `reusable-pages-static.yml`, whose guard additionally requires a push
on the repository default branch. Artifact directory is `site`; permissions are
contents:read, pages:write, id-token:write. Environment is `github-pages`.
Draft PR checks cannot publish. No workflow change is required for these fixes.
The caller hardcodes main; if the default branch ever changes, align the caller
with it. Reusable-workflow-only changes do not match the current path trigger.

Before an authorized publication, verify Settings > Pages source is GitHub Actions
and environment protection allows the main deployment. The repository metadata
reports has_pages=true, which does not establish the Pages source or successful
publication of this dashboard. Those settings and a live dashboard deployment
were not verified with the available read endpoints. After an authorized merge,
verify the Parent audit Pages run, upload/deploy jobs and environment URL; then
check `index.html`, `sync.js`, `app.js`, `data.json` under the parent project path
`https://myon-bioinformatics.github.io/myon-bioinformatics/`, including a reload
with pre-existing storage. Do not use the portfolio root URL as the target.
