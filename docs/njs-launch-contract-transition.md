# njs_launch consumer contract transition

The catalog preserves both launcher names. This change stages desired `njs_launch.py`
paths for MCP #210, PSWB #143, markdown #150 and xprobe #56. Existing recorded
observations retain their exact old lock/topology through digest-bound transitions.
No audit engine, source-byte, placed-file, identity or first-parent check changes.
The existing MCP PSWB transition keeps its same recorded SHA, old entries and
complete lock digest; its desired-contract digest advances to include this rename.

PSWB separately already removed gh_identity copies and moved the Ironmate license
from `tool/vendor/LICENSE` to `tools/vendor/LICENSE` before #143. Its immutable
base `db42f6bd464d1553add302d7fcfbd88da338c7ef` contains only that license and the
two launchers in these vendor directories. All three placed blob IDs match its
lock. License pin `73157cb7fed236a4a941722a6dcddd69a33ab95a` and blob
`4ec4989b801bf1a3df6184d21f79e6e7ed5931f6` are unchanged. This registry alignment
records that existing cleanup, not a new deletion. Its old observation stays frozen.

Merge parent after CI, recheck/merge children with any separately approved stacked
changes, then audit each actual merged child before refreshing observations and
retiring active transitions. Do not delete the old catalog row until the registered
and observed contracts no longer depend on it. Historical fixtures stay unchanged;
new fixtures distinguish reviewed target locks from actual adopted observations.
