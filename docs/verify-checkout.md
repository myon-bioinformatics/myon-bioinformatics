# Verify one immutable Git revision

`tools/verify_checkout.py` turns the post-merge sequence (resolve main, check an
expected ancestor, run stdlib tests, then pytest) into a reusable stdlib-only CLI.
It accepts any local Git repository and ordered JSON argv arrays. No shell parses
the commands; `{python}` as a complete argument selects the invoking interpreter.

Fetch explicitly, then verify the fetched revision:

```sh
git fetch origin main
python -S tools/verify_checkout.py . --ref origin/main \
  --contains bcdb446f90cf2fcbba55ba846cc9d70740745042 \
  --command '["{python}","-S","-m","unittest","discover","-s","tests","-p","test_constrained_lab*.py"]' \
  --command '["{python}","-m","pytest","-q"]'
```

The ref is resolved once to a commit and tree SHA. Each `--contains` requires a
full commit SHA; every ancestor must be available locally and reachable from the
resolved commit. This is ordinary ancestry, not a first-parent or squash/rebase
equivalence check. Shallow clones may need an explicit fetch/deepen first.

Only after these checks pass does the verifier create a disposable detached
worktree. Commands run in order there, stopping at the first nonzero exit,
timeout, or launch failure. A failed ancestry check launches nothing. The original
checkout may be dirty: its branch, index and files stay in place. The temporary
worktree is removed, including test artifacts; arrange external artifact paths
in trusted commands when preservation is needed. Submodules and Git LFS content
are not initialized. Dependencies must already exist in the invoking environment.

The single stdout JSON receipt (`checkout-verification/1`) records the requested
ref, actual commit/tree, required ancestors, each attempted command and its status,
exit code, and final 8 KiB of stdout/stderr with truncation flags. It also checks
that the worktree HEAD still matches the tested revision after commands finish.
Git/Python path overrides are removed from the subprocess environment. Logs spool
to disk, so output memory is bounded, but disk use is not capped.

Exit 0 means all required ancestors and commands passed at that revision; exit 1
means an ancestry/test/timeout/launch failure or changed test HEAD; exit 2 means
invalid input, unavailable Git objects, or Git/setup/cleanup errors. No empty
command list can succeed. `--timeout` is a finite positive per-command timeout
(300 seconds by default). It bounds the direct command, not its descendants.
This executes trusted local commands and provides no resource or network sandbox.
It does not fetch, install packages, switch the caller's branch, push, or merge.

Use this for a consumer by changing the root, ref, ancestor and command arrays;
the generic Git/worktree/receipt logic stays in the parent. Repository test plans
and wheel-specific golden assertions remain with their owners. For a wheel test,
pass the existing build and installed-distribution test commands in order, so a
failed build cannot be followed by a misleading test of an older wheel.
