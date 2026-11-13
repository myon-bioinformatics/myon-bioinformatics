# MCP PSWB module contract migration

MCP lab PR #209 renames the copied PSWB module and license to
`vendor/pysaiyanweboo.py` and `vendor/pysaiyanweboo-LICENSE`.
The source is pinned to PSWB commit
`bbe5c0b0087f40ff3fffd53bec5111c2ba2d747d`.

The desired consumer registry adopts those paths. The parent still records
MCP observation `cd04472040f0db991c5138144c01f56a2b79e44b`.
The explicit transition preserves that exact old observation's complete lock
and old topology for strict audits. It is bound to repository ID, observation
SHA, complete lock digest, and normalized desired consumer digest.
No source-byte, placed-file, immutable-pin, or first-parent check is skipped.
Any new observation must match the desired topology; changed historical locks
or changed contracts fail closed.

Order: merge this parent contract after CI; revalidate and merge MCP #209;
strictly audit the actual merged child snapshot; then refresh the observation
and remove this active transition in a separate reviewed cleanup. The old/new
fixtures remain historical regression inputs, not claims of current adoption.

This does not reconcile the pre-existing Aoi or Portfolio registry drift,
and does not change the existing Ironmate transition or audit engine.
