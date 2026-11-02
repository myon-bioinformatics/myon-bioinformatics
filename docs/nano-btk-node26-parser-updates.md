# Additive Node 26 parser snapshots

These explicit dependency-only profiles extend, rather than replace, the historical
BTK and Node26 snapshots. Existing measured recipes and Bun pins remain unchanged.

| Feature suffix | Exact BTK input commit | Parser versions |
| --- | --- | --- |
| `node26-marked` | `d119b57b14bd8032dd220dfda0ee613106131260` | marked 18.1.2, linkedom 0.18.13 |
| `node26-parsers` | `45a3da49ce522519817dc2a207d849c51e0328d1` | marked 18.1.2, linkedom 0.18.14 |

The full feature prefix is `browser-test-kit-document-mirrors-`. The second source
is a normal merge preserving both dependency PR histories and current Node26 main.
Only package.json and package-lock.json are selected; live checkout tests are never
replaced with snapshot tests. Both profiles require `--dependencies-only`.

Each browser projection retains exactly 30 complete package records. The combined
profile replaces cssom 0.5.0 with @acemir/cssom 0.9.32; it retains linkedom's exact
optional canvas peer metadata without installing canvas. Registry locations,
integrities, dependency edges and engines remain strict. ExcelJS 4.4.0 remains in
the original full lock and separate consumer smoke lane, not this browser subset.

Preparation invokes offline official npm lock-only projection, never installation.
Receipts explicitly report no installed package count or bytes. Consumers must
independently verify the merged helper commit/hash, use the matching explicit
selector, install with scripts disabled, verify all installed paths/versions, and
run tests from their live checkout. This is compatibility evidence, not a new
benchmark or a claim that historical measurements apply to updated dependencies.
