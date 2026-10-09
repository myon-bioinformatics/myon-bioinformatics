# Shared TypeScript compiler direction

Decision: 2026-10-09. For repositories that need TypeScript type-checking or
compilation, adopt the stable Go-native TypeScript compiler line (TypeScript 7+),
using a reviewed exact package version and the consumer's lockfile. Python remains
the default for Python tooling; JavaScript-only/stdlib repositories do not acquire
a TypeScript build just to conform.

Microsoft's [TypeScript 7.0 announcement](https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/)
dated 2026-07-08 confirms a stable Go-native port distributed through the normal
typescript package and tsc executable. This policy targets that stable line, not
the historical native-preview package or an unpinned nightly. Do not infer the
latest patch from the announcement; resolve and record it at adoption time.

The native implementation accelerates compiler/tooling work. It does not mean
browser JavaScript runs as Go or that Deno automatically uses this compiler.
Preserve browser output targets and runtime compatibility separately.

## Consumer adoption

- Inventory tsconfig, TypeScript package/lock, CI commands and compiler-API users.
- Select a current stable compatible native release, pin it and record tsc --version.
- Run existing type-check/build/runtime checks at the same head before merging.
- If a tool requires the old TypeScript compiler API, retain an explicit scoped
  compatibility path rather than pretending it migrated. The 7.0 announcement
  documents a separate TypeScript 6 compatibility package and aliasing approach;
  recheck the current release's API availability during implementation.
- Keep web-ui consumers build-free unless they already require a compilation
  step; generated browser assets remain versioned and managed through existing
  vendor ownership.

This PR records the cross-repository rule. It does not claim consumer package
upgrades, compatibility checks, runtime benchmarks or a completed rollout.
Each consumer migration should identify its pinned release and validation result.
