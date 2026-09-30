# Shared JUnit failure identity

The reusable collector consumes `junit-*` artifacts from the current workflow run.
Report production stays in each repository. Keep pytest's exit code, disable
matrix fail-fast when collecting all jobs, and upload raw XML with `if: always()`.
Use distinct artifact names for every job/platform/Python version.

After this workflow is merged, replace `FULL_COMMIT_SHA` with its immutable merge
commit. Do not use a floating branch or invent a commit pin before merging.

```yaml
  failure-identity:
    needs: [test]
    if: always()
    permissions:
      contents: read
      actions: read
    uses: myon-bioinformatics/myon-bioinformatics/.github/workflows/reusable-junit-identity.yml@FULL_COMMIT_SHA
    with:
      expected-reports: '["junit-py3.12/pytest-3.12.xml", "junit-py3.14/pytest-3.14.xml"]'
      artifact-pattern: "junit-*"
      output-artifact: "failure-identity"
```

`expected-reports` is required: a JSON array of exact paths after artifact
extraction, including the artifact directory and XML basename. Keep this list in
sync with the producer matrix. Only valid, non-truncated reports satisfy a slot;
zero-byte or malformed XML cannot. A valid zero-test JUnit suite is accepted.
Missing expected paths and unexpected XML both fail collection, so an extra
report cannot replace a missing matrix leg. Duplicate/empty/unsafe expectations
are rejected. The summary names missing configured paths; unexpected paths remain
hashed. The import step runs even after download failure to preserve incomplete
evidence where the pinned importer is available. This is a required-input change:
repin callers and supply the expected set together.

For a single expected artifact, download uses its exact name and an explicit
artifact subdirectory: download-artifact v8 otherwise flattens a one-artifact
match. Multi-artifact collection preserves separate directories. If only one
artifact survives a multi-artifact expectation, its flattened paths cannot
satisfy the expected set and collection remains incomplete; no artifact identity
is guessed from filenames.

The collector verifies a fixed xprobe Git blob before executing the importer.
It never checks out or executes the caller's code. Raw reports stay separate;
only compact failure identities and collection completeness are uploaded here.
Messages, tracebacks, parameter labels, arbitrary report paths and system output
are omitted. Failure and error remain distinct; skip/xfail are not promoted to
failures. This collector does not infer skip versus xfail details from XML.

Repository context is the caller's `github.repository`; commit SHA remains null
until canonical producer metadata is wired in. `github.sha` is not substituted.
Hashed relative artifact paths distinguish reports, including equal filenames
from different matrix artifacts. Inputs needed to reproduce a failure are not
reconstructable from JUnit and must be attached separately.

At most 100 XML reports, 1 MB per report, 20 MB total, and 1000 failure identities
per report are consumed. Missing XML, invalid input and truncation produce a
nonzero collector exit and a `complete: false` collection summary. Successful
collection of failing tests returns zero; the producer job remains failed.
`if: always()` preserves compact evidence even when collection is incomplete.
Artifact extraction occurs before these XML parsing limits; GitHub artifact
download limits are separate. Raw JUnit is never added to Pages by this workflow.

Local tests execute the workflow's actual Python body:

```sh
python -m pip install -r tests/junit-requirements.txt
XPROBE_SOURCE=/path/to/pinned/xprobe.py python -m pytest -q tests/test_junit_collection.py
actionlint .github/workflows/reusable-junit-identity.yml .github/workflows/junit-collector-test.yml
```
