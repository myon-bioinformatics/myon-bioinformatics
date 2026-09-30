# GitHub Actions CLI wrapper

`gh_workflow.py` is a single-file Python 3.10+ module/CLI using only the standard
library. The external runtime dependency is authenticated GitHub CLI (`gh`).
Install/authenticate `gh` separately; no token is stored by this wrapper.
It targets github.com explicitly and forwards API requests through `gh api`.

## One command for preflight, enable and dispatch

Read-only plan (the default):

```bash
python gh_workflow.py myon-bioinformatics/convert_img_fmt_to_webp-CUI- codeql.yml --ref dependabot/github_actions/actions-3e14a33e79
```

Enable if disabled, then dispatch:

```bash
python gh_workflow.py myon-bioinformatics/convert_img_fmt_to_webp-CUI- codeql.yml --ref dependabot/github_actions/actions-3e14a33e79 --apply --receipt reports/workflows.jsonl
```

`--inputs '{"run_playwright": true}'` forwards a JSON object via subprocess stdin;
values beginning with `@` remain literal and never load local files. Inputs are
not copied into receipts. Omit `--ref` to use the repository's default branch.
The API accepts branch/tag names for dispatch. Preflight records their resolved
commits, reads YAML at those immutable SHAs and verifies the returned run's
executed SHA, exposing a branch movement as `dispatched_ref_changed`.

Preflight checks the workflow state and `workflow_dispatch` on both default and
target definitions. If the trigger is absent, add it to the YAML and get the
definition onto the default branch before retrying. The wrapper does not edit
YAML, merge PRs or decide whether CodeQL is a required merge check.

## YAML recognition boundary

To preserve stdlib-only distribution, this is a conservative trigger recognizer,
not a general YAML parser. Supported forms are `on: workflow_dispatch`, an inline
event list, and a block event mapping with bare/quoted keys and nested options.
Comments and CRLF are supported. Flow maps, inline event configuration, block
event lists, aliases/anchors, block scalars anywhere in the file, YAML document
markers/directives and duplicate event keys yield `*_yaml_unknown`. These are
blocked before any mutation. A name appearing in a comment, job, path or input
is not treated as a trigger. GitHub still validates the actual workflow and
input definitions; successful preflight does not guarantee dispatch acceptance.

## Receipts and failures

Each invocation prints one JSON object. `--receipt` also appends it to JSONL
(sequential writers; concurrent append locking is not provided). Receipts contain
workflow state, observed commits, reasons, completed enable operation and dispatch
run ID/URL. Current API responses provide the exact run ID. Older empty responses
yield `dispatched_run_unresolved`; no newest-run heuristic is used. Run observation
is a single GET and does not wait for CI to finish. `conclusion: null` means the
run has no measured conclusion yet; dispatch acceptance is not CI green.

Read-only `planned` and verified `dispatched` return exit code 0. Blocked,
unresolved, uncertain or failed results return 1. Invalid CLI syntax returns 2.
`--timeout` defaults to 30 seconds per `gh` call. No operation automatically
retries, enables another workflow or broadens token permissions. After a mutation
timeout/5xx, inspect Actions before retrying: the request may have succeeded.
After an observation failure the exact run ID/URL remains in the receipt.
Authentication errors, 403 (permission/rate-limit), 404 (missing/inaccessible)
and 422 (rejected dispatch) are classified without storing raw stderr.
The authenticated account needs read access for preflight and Actions write
access for enable/dispatch. Authentication remains `gh`'s responsibility.

Output is buffered by `subprocess.run`; timeout limits duration, not memory.
Only named workflow operations are exposed, with argv arrays and `shell=False`.

## Verification

```bash
python -m pip install -r tests/workflow-requirements.txt
python -m pytest -q tests/test_gh_workflow.py --junitxml=reports/workflow-wrapper.xml
```

Tests mock the external process so CI never enables or dispatches workflows.
PyYAML is installed only through the test requirements and serves as an
independent YAML oracle for supported forms and valid forms outside the subset.
Its non-constructing BaseLoader preserves `on` as a string (the default YAML 1.1
loader would treat it as a boolean). The runtime module never imports PyYAML.
The separate test requirements and workflow avoid depending on the pending Git
inspector PR. CI covers Python 3.10–3.14 and preserves JUnit with `if: always()`.
The existing reusable JUnit collection is available for subsequent rollout;
this PR does not alter that contract or other repositories' workflows.

Official references:

- https://cli.github.com/manual/gh_api
- https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
- https://docs.github.com/en/rest/actions/workflows#enable-a-workflow
