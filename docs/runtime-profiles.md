# Nano runtime profiles: planning only

Start with stdlib Python / Node built-ins and add native capabilities only when
their implementation benefit justifies the dependency and maintenance cost.
This first slice describes two configurations; it does not install, benchmark,
launch workers, replace Docker, or distribute anything to children.
See the [small-runtime principle](small-runtime-principles.md).

## Two initial configurations

| Profile | Runtime requirements | Optional / separate work |
|---|---|---|
| `stdlib-core` | Python 3.9+ and its `json` module; no third-party package | Parent-owned `node_launch.py` / `py_launch.cjs`, with Node 24+ only when the bridge is needed |
| `aoi-numeric-native` | Aoi `shrink/numpy_min.py`, Python 3.11+, `ctypes`; candidate target Debian 13 / amd64 / glibc, LP64 32-bit integers, listed BLAS/LAPACK symbols and package versions | Node bridge is not needed for numeric work. Research-wide NumPy/Polars, browser, model and developer environments are separate |

`runtime-profiles.json` is descriptive metadata, **not a second adoption ledger**.
The core source references point to existing advisory `vendor-catalog.json`
entries, not duplicated commits. The planner does not resolve or verify those
references. Actual consumer adoption still uses `vendor-consumers.json`,
`child-heads.json` and `vendor-lock/1`, with source/byte/license checks. None of
those records change here. See [vendor automation](vendor-automation.md).

The Aoi reference is explicitly **not adopted**. Its source URL is frozen at
the reviewed upstream merge SHA. Package versions reproduce the declarations
in the [upstream backend workflow](https://github.com/myon-bioinformatics/Aoi/blob/2f6907f69856a0f1b1570a039d1feefcf6b2edea/.github/workflows/numpy-min-backends.yml):
`liblapack3=3.12.1-6`, `libblas3=3.12.1-6`, `libgfortran5=14.2.0-19`.
Debian 13 / amd64 is the deliberately narrow candidate target here, **not a new
claim that this planner verified that platform or any library binary**. Ubuntu,
other releases/architectures, musl and ILP64 require separately reviewed profiles.
SONAME, complete Fortran calling convention, library origin/hash, loader search
path and transitive dependencies remain unverified; symbol names and integer
width alone do not certify an ABI. No `.so` is loaded by this planner.

The source can use a pure-Python fallback, but this profile specifically requests
native capability. A mismatch is reported, not silently changed to a fallback.
Its [decomposition-v2 contract](https://github.com/myon-bioinformatics/Aoi/blob/2f6907f69856a0f1b1570a039d1feefcf6b2edea/docs/contracts/numpy-min-acceptance-v2.md)
does not promise complete NumPy compatibility, identical rankings or universal
numerical error bounds. A pure backend needs its own explicit selection and tests.

## Read-only CLI and Python API

All facts are explicitly supplied. The included fixture is **invented test data**,
not an inventory of this machine or proof that packages are installable.

```sh
python -S tools/runtime_plan.py --profiles runtime-profiles.json \
  --profile aoi-numeric-native \
  --environment tests/fixtures/runtime_plan/synthetic-debian13-amd64.json
```

Use `tests/fixtures/runtime_plan/unknown.json` to see an honest unknown result.
The API is `tools.runtime_plan.plan(manifest, profile_name, environment)` and has
no file access. The CLI reads only the two explicitly named JSON inputs and writes
JSON to stdout (errors to stderr). It does not scan the host, read environment
variables, import described sources, load native libraries, spawn commands,
access the network, resolve packages, install, or modify files. Input metadata
is never a command or executable path. It is not a sandbox for other programs.

| Output / exit | Meaning |
|---|---|
| `static_requirements_status=requirements_match`, exit 0 | Supplied facts meet the listed **runtime** predicates only |
| `missing`, exit 1 | At least one runtime fact explicitly says the capability is absent |
| `unsupported`, exit 1 | A supplied runtime fact does not meet the selected version/platform/ABI contract |
| `unknown`, exit 1 | A required fact is missing or null, with no stronger failure |
| `nano-plan-error/1`, exit 2 | Invalid JSON/schema/profile or unreadable input; CLI usage errors also exit 2 |

Precedence is unsupported, missing, unknown, then requirements_match. Every
predicate remains in the result, so stronger failures do not hide unknowns.
Optional/build/development results never masquerade as runtime prerequisites.
`execution_verification=not_performed`, `measured=false` and
`execution_ready=false` apply to **every v1 plan**, even one with matching facts
or `kind=provided`. These are comparisons, not authenticated observations.
`dependency_closure`, `package_hashes`, `security_review` and `measurements` must
remain JSON null in v1. Sources/licenses are not verified. Future verified
execution evidence needs a separate explicit contract, not a caller-supplied
`ready=true` flag or an implicit reinterpretation of exit 0.

## Small v1 schema

- Manifest: exactly `schema: nano-profiles/1` and nonempty `profiles`.
- Each named profile: `description`, `sources`, `requirements`, `evidence`.
- Requirements have four explicit arrays: `runtime`, `optional`, `build`,
  `development`. Runtime cannot be empty. Each predicate has `id`, `fact`, `kind`,
  `expected`. Kinds are `minimum_version` (numeric two/three-part release),
  `equal` (string or nonnegative integer), `contains_all` (symbol strings), and
  `present` (`expected=true`). No expressions, shell, templating or imports.
- Source: `repository`, relative `path`, `scope`, `ledger`, `evidence_url`.
  Either reference `vendor-catalog.json` with no duplicate source pin, or use a
  null ledger with a GitHub blob URL naming that same source at a full commit SHA.
  A URL is evidence metadata only, never fetched. No `main` URLs or traversal.
- Environment: exactly `schema: nano-environment/1`, `kind: synthetic|provided`,
  `facts`. Fact keys must be declared by the supplied manifest. Omitted/null
  means unknown; false means explicitly absent. Other values have the predicate's
  required type. Boolean true is not an integer ABI width. Presence uses true.
- Unknown fields, duplicate JSON keys/IDs, invalid paths/types, non-finite JSON,
  excessive nesting (>32), input bytes (>1 MiB), or lists/objects beyond their
  documented validator bounds fail closed. Each list and the profiles/facts
  objects are limited to 128 entries. Input files must be regular, non-symlink
  files; this is not a filesystem containment or concurrent-writer guarantee.

The stdlib validator in `tools/runtime_plan.py` is the executable schema.
The profile's Python floor describes compatibility, **not a security support
recommendation**. Interpreter security status must be assessed separately.

## Installation, updates and replacement boundaries

1. Keep source runtime, OS runtime closure, build tools and developer/oracle tools
   separate. Empty `build` here means this slice introduces no compile step; it
   does not mean CPython/Node or their OS dependencies need no build/runtime.
   The `development` predicates cover this planner's pytest lane, not the entire
   Aoi research environment. Aoi's default `uv sync` includes broader dev/runtime
   groups and is not a minimal deployment recipe.
2. Before any actual native installation, resolve the full package dependency
   closure for the exact distro/release/arch using its trusted repositories;
   retain package hashes, origin and license notices. `--no-install-recommends`
   does not eliminate mandatory dependencies. Do not copy one `.so` and assume
   the closure, ABI, licensing and security obligations have disappeared.
3. Review security updates even when the compatibility floor stays unchanged.
   Exact package mismatches, including newer patches, currently return unsupported:
   that means this frozen example needs review, not that a newer version is bad.
   Do not silently fall back to another package, mutable ref, distro or architecture.
4. After separately authorized verification, stage a new version, test its
   contract, direct new jobs to it, drain old workers and retain a rollback
   reference. Do not overwrite a loaded library in place. Updating system apt
   packages can affect other consumers; per-source version directories do not
   isolate system libraries. No automatic updater is added here.
5. Worker count, BLAS internal threads, buffers and model/browser memory must be
   budgeted together. Existing [constrained-lab](constrained-lab.md) is a trusted
   process harness, not Docker-equivalent isolation. Neither apt-over-wheel size
   savings nor process-over-container speed/memory gains were measured here.

## Verification

```sh
python -S -m unittest discover -s tests -p 'test_runtime_plan.py' -v
python -S tests/stdlib_runtime_smoke.py
```

Tests use JSON fixtures, fake facts and temporary files only; they do not execute
the described workloads. Existing Python CI collects the tests on 3.11–3.14;
the existing 3.9/3.10 stdlib smoke also covers the planner. Manifest changes
trigger that same workflow. No new CI measurement, apt step or child rollout.
