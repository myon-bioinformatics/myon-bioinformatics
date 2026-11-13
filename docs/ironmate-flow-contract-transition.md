# Ironmate flow contract transition

Ironmate #223 moves the existing PSWB flow implementation to the already merged
ascii_artist implementation. Parent #259 changes the desired enrollment contract.
The parent's recorded Ironmate observation still points to commit
`3759429eae7138c952093c1e596b2c6414a58d0f`, whose 61-file lock uses PSWB.

`vendor-observation-transitions.json` binds that one immutable observation to its
complete previous contract. It does not authorize new children to enroll against
the old contract. It does not skip source, blob, digest, placed-file or first-parent
checks: the existing adopted-file auditor performs those checks on every old file.
The exact repository identity, observed SHA, whole lock digest and desired consumer
contract digest must match. Changed or mixed locks and failed reads fail closed.
An observation at any other SHA receives normal desired-contract auditing.

## Rollout and removal

1. Verify the old observation with the transition, and verify the proposed new lock
   against the desired registry. Resolve unrelated failing CI before merging.
2. Merge the parent registry and transition together, then verify parent main CI.
3. Re-run Ironmate #223's enrollment audit against the merged parent registry.
   Merge #223 only after its required checks succeed, and verify its main CI.
4. Fetch Ironmate's merged main and immutable lock. Verify identity, full topology,
   source first-parent membership, source bytes and all 55 placed files.
5. Update `child-heads.json` and the current reviewed observation fixture together.
   Remove Ironmate's transition entry (and the manifest if empty), and remove the
   temporary `POST_BASELINE_REPLACEMENTS` row from the current-baseline test path.
   Keep historical fixtures and their historical-contract assertions intact.
6. Confirm strict parent and child main audits without any transition entry.

Until step 5, the parent report explicitly lists `observation_transitions`.
This is a statement about the frozen observation, not a claim that Ironmate has
already adopted the new contract. A child merge alone does not update the parent's
recorded observation. Never merge with a failed audit merely to unblock the other
repository.
