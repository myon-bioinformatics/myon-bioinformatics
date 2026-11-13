# PR #258: key promotion validation (2026-11-13 JST)

## Reviewed revisions

- Requested head: `13376975be5236a4137636637b79e813ff90f4f3`.
- Final PR head: `0726b9ee8a9d7542e3d77b95ff0d46ceb4a6ce5d` (adds per-id repeat/collision findings).
- PR already merged at 2026-11-13 08:51:01 JST: `a0273711386572f8103e4d2e1924d0983d7622e3`.
- Local correction based on main `85c528d` on `fix/fleet-key-validation`.
- No remote writes or merges performed by this review.

## Findings and local corrections

1. A known collision was labelled `incomplete` whenever any entry was missing a candidate field.
   Count retention alone is insufficient for consumers that select by verdict. Prioritize `collides`,
   retaining the missing count. Existing corpus duplicate-id findings already reach audit remaining items.
2. String conversion equated numeric `1` and string `"1"`, and other JSON values with equal display text.
   Group by canonical JSON values instead; retain old display labels for examples.
3. Empty observations returned `key`. Return `incomplete`: no observed evidence supports promotion.
4. An input iterator was consumed by the first candidate; later candidates could return empty `key`.
   Materialize once for all candidates.
5. Corpus identity reports excluded entries without `id` before checking completeness.
   Include all dictionary entries in the identity report; keep existing graph entry handles unchanged.
6. Empty/duplicate candidate fields and `+` in names made candidate report labels ambiguous.
   Reject these candidates explicitly.

The output fields and group counts remain unchanged. Intentional behavior changes are described in
`fleet-records.md`. Collision and repeat counts count disjoint groups; within a colliding group, a repeated
copy is not separately counted as a repeat group. There is no automatic historical identity registry:
demotion means recomputing a report from the enlarged set of observations. Existing returned snapshots
are not mutated. Examples remain human-readable labels, not identities.

## Actual corpus reproduction

Repository: Err-o-RoadRunner, commit `92e89e3a6e02c4008ecda9d2fc87fcf4c7768532`.

File: `tests/fixtures/junit_corpus/classification.json`.

SHA-256: `7223dda88e3080051b9608840f4656ab034e674ff2e107e3bfcf97d80d3e1ef3`.

| Candidate | Verdict | Distinct complete groups | Repeat groups | Collision groups | Missing entries |
| --- | --- | ---: | ---: | ---: | ---: |
| id | collides | 451 | 0 | 7 | 0 |
| id+source_file | incomplete | 357 | 0 | 0 | 101 |
| repo+run_id+artifact | collides | 450 | 0 | 8 | 0 |

All seven conflicting ids have two entries each, with different source_file and sha256 values.
The adapter read all 458 goldens: FOUND 5958, NOT FOUND 2627, HINT 1282; no missing references,
no truncation, seven duplicate-id findings. It retained 451 observed_in and 451 derived_from relations,
with zero dropped relations. Existing handles and graph deduplication remain unchanged; this correction
does not split corpus nodes or rewrite source records.

From the parent checkout, with a sibling checkout of Err-o-RoadRunner at the above revision:

```sh
python -S - <<'PY'
import hashlib, json
from pathlib import Path
from fleet_records import key_report
p = Path('../Err-o-RoadRunner/tests/fixtures/junit_corpus/classification.json')
data = p.read_bytes()
assert hashlib.sha256(data).hexdigest() == '7223dda88e3080051b9608840f4656ab034e674ff2e107e3bfcf97d80d3e1ef3'
rows = json.loads(data)
assert len(rows) == 458
print(json.dumps(key_report(rows, [('id',), ('id', 'source_file'), ('repo', 'run_id', 'artifact')]), indent=2))
PY
```

## Validation and boundaries

- Eight new focused tests pass; five expose failures when only key_report is restored to the pre-fix function.
- The existing mixed missing/collision fixture now expects collides and checks both counts.
- Additional integration test confirms duplicate-id reaches remaining audit items and that split_fleet
  retains verdict, missing/collision counts and findings while keeping dependency edges intact.
- Pytest: 99 passed, 1 skipped, 17 subtests passed for fleet records, fleet graph, fleet plan and audit Pages;
  the integration test added afterward passed separately under standard-library unittest.
- Final `python -S -m unittest tests.test_fleet_records tests.test_vendor_fleet_graph tests.test_audit_pages`:
  93 tests run, 92 passed, 1 skipped, including the additional integration test.
- The skipped check requires all real fleet checkouts. This review fetched only parent and Err-o-RoadRunner;
  it does not claim a complete real-fleet audit or browser visual verification.
- Upstream final PR head: all five Actions workflows successful. Merge commit: all seven observed workflows
  successful. These upstream runs do not validate the unpushed local correction.
- No dependency, workflow, deployment, lock or child-repository changes. Key/graph logic remains parent-owned.

Because #258 is already merged, it cannot receive another mergeable update. The prepared correction needs
one follow-up PR; publishing it and later merging require the user's explicit approval.
