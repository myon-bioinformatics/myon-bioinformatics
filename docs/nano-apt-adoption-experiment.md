# apt helper adoption experiment (2026-11-05 JST)

Decision: retain Ironmate #203's workflow for now; add offline comparison coverage only.
No OS package was installed, no workflow was edited or dispatched, and no merge was performed.

## Immutable inputs and dependency order

- Parent #212: 819eb4221ef72b2a36c82c6910ce59b7b69c10b6; base #211 branch fix/explicit-validation-optimized at 424bfa4ef953baf34e4ee247f18b8f187b253499.
- Ironmate #203: 77b4a584795c0fe2f8a21f4b5a352395f6941d22; base #202 branch feat/png-zoom-return-gif. Its recorded base was 8a21a84b2882e64d4e15818d03d19b1c10eda106; #202 had head 73b90ab0fc2bc389947cb44c9a8edc2fd7c99503 at the original snapshot. Since then #202 merged at 0ab61e44a8a593c526a4f94381a88822406843eb and #203 was retargeted to main; its package-list head remains unchanged.
- Ironmate checks out parent vendor tooling and reusable JUnit workflow at 9e3da7ae1846e9604ebc24bb5178f7de094a4e77.
- Ironmate vendor.lock.json has no tools/nano_apt.py entry. Parent vendor-consumers.json has no nano_apt adoption for Ironmate.
- Parent child-heads.json records Ironmate 8f4b9e3e2b12a2a32c47daa37da0a5bc3ee7dcd3, not #203.
- Therefore existing locks do NOT deliver this helper. An adoption must wait for an accepted default-branch first-parent source commit, then explicitly register source/destination/license, commit/blob/SHA-256, and deliberately review the child baseline. Do not pin an unmerged #212 head into production locks.
- Parent helper imports only stdlib and does not import Ironmate. Keep this one-way source dependency; parent adoption tests use frozen package strings, never execute/fetch a child workflow.

## Behavior comparison

| Property | Ironmate #203 grep/xargs | Parent #212 helper |
| --- | --- | --- |
| Actual package groups | fonts-noto-cjk; python3-uno, libreoffice-impress | Same set; accepted by parser |
| Deduplication | No reader deduplication | Within/across lists; conflicting versions rejected |
| Ordering | apt config, update, fonts, fc-match, UNO, soffice | Separate calls preserve group order, but UNO arguments sorted libreoffice-impress then python3-uno |
| All packages already installed | Always update + install invocations | dpkg-query only; no apt invocation |
| Both groups missing | One update, two installs | Separate calls: two updates, two installs; merged: one update, one install but changes checkpoints/group order |
| Verification | fc-match and soffice, no package-state recheck | dpkg-query after successful install; retain functional checks |
| apt failure | Bash pipeline fails; xargs normally maps child exit 1..125 to 123 | Update/install failure mapped to exit 3; stops immediately |
| Report-only missing | No report mode | exit 1; malformed/missing input exit 2 |
| apt unavailable | Command failure | exit 0 apt_unavailable: unsuitable as a mandatory CI guarantee without strict handling |
| Empty/comment-only file | grep returns 1 (but xargs can still invoke apt with zero operands) | accepted as satisfied; empty observe queries the installed database |
| Query database error | No query | fatal return codes fail closed with exit 2 before any install attempt |

The shell reader accepts a narrower/different format than the Python parser: whitespace-prefixed comments are not removed by grep; whitespace-only files differ from empty files; xargs interprets quotes/backslashes. The checked-in simple lists avoid these differences. Missing/comment-only input failure does not imply no child process was launched.

Recommended prerequisite work before actual CI adoption: strict missing-tool and empty-list policy, decide how to preserve the single update plus fonts/UNO checkpoints. Do not add skip-update flags or caching without a demonstrated requirement and tests.

## Verification and size

The new unittest module reuses the existing fake command runner and base contracts. All simulated install paths intercept subprocess.run. The inherited real read-only dpkg test is overridden with a hard guard that rejects every executable other than dpkg-query.

Command: python -S -m unittest discover -s tests -p test_nano_apt_adoption.py -v
Updated integration result in the repair environment: 41 tests passed; the inherited real-read-only observation failed because the local dpkg database does not contain dpkg. Its hard subprocess guard remains unchanged. All 7 adoption-specific cases pass. Hosted verification remains pending.
No new runtime code, third-party dependency, lock entry, workflow, checkout, or permission added. Only a comparison test and this report are added. The source helper has grown since the original experiment; vendoring it would add its implementation plus lock/provenance maintenance, so this is not automatically a code-size simplification.
No install timing, disk size, or CI speed measurement was performed; command counts are mock observations, not performance claims.
The full parent suite and remote CI were not run locally.

## Other repositories: discovery candidates, not rollout

GitHub code search for apt-get install returned 25 file hits across default-branch indexed snapshots. This is not an exhaustive inventory of every branch, apt alias, or dynamically generated command.

- web-ui .github/workflows/successor-studios.yml: pulseaudio/pulseaudio-utils, followed by daemon and sink setup. Candidate for a small list; preserve service setup and verify package state. No trial workflow change while adoption prerequisites remain.
- QueRyu .github/workflows/ci.yml: liblapack3/libblas3. Candidate, but current flags allow recommends; adopting --no-install-recommends changes policy and needs separate validation.
- Aoi ci.yml/numpy-min-backends.yml: pinned BLAS/LAPACK, apt-cache diagnostics, simulate and --no-remove safeguards. Keep existing specialized sequence; helper lacks equivalent protections.
- flutter_navigation_basic non-dart.yml: tzdata/git bootstrap and apt-list cleanup; avoid making checkout/bootstrap depend on an unavailable vendored helper.
- Flutter Dockerfile.e2e: installs python3 itself and adds NodeSource between installs. Cannot replace initial bootstrap with a Python helper.
- Pages Dockerfile.html-import similarly installs python3 into a Node base. Keep bootstrap.
- mcp-toolcall-lab: several Dockerfiles repeat a pinned ca-certificates install, with same-layer apt cache cleanup. A shared text list may reduce repeated names, but helper use requires COPY/build context changes and retains cleanup; no measured benefit yet.
- cli_args tests/docker/Dockerfile: container utilities in one apt layer; list extraction possible, but no repeated installation to skip within that layer.
- Aoi/queryu is a nested copy of QueRyu; do not count it as another independent rollout.
- Parent hits are fixture copies, not active apt jobs.
- Playwright install --with-deps is intentionally left under Playwright management.

No source lock hashes were changed or re-certified. This audit establishes the missing enrollment, not a new fleet-wide provenance certification.
