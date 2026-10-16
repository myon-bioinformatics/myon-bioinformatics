#!/usr/bin/env python3
"""Apply explicit per-repository mboxes with git am --3way in new worktrees.

The default is a read-only plan. --apply creates local feature branches only;
there is no fetch, push, PR, merge, lock generation, or scheduled update.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def git(root: Path, *args: str) -> str:
    # Do not let a caller's Git routing variables select another index/worktree.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    proc = subprocess.run(
        ["git", "-C", str(root), *args], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace", env=env, timeout=120,
    )
    if proc.returncode:
        raise ValueError(f"git {args[0]} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.rstrip("\r\n")


def plan(targets: list[list[str]], branch: str, output: Path) -> list[tuple[Path, Path, Path, str]]:
    """Check the entire explicit batch before creating any branch or worktree."""
    result = []
    seen_common = set()
    seen_destinations = set()
    for checkout, mailbox in targets:
        root = Path(checkout).resolve(strict=True)
        patch = Path(mailbox).resolve(strict=True)
        if not patch.is_file() or not patch.stat().st_size:
            raise ValueError(f"patch must be a non-empty file: {patch}")
        if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
            raise ValueError(f"checkout must be the repository root: {root}")
        # Disallow branch shorthand such as @{-1}; this must be a new literal name.
        if git(root, "check-ref-format", "--branch", branch) != branch:
            raise ValueError("branch must be a literal new branch name")
        common = Path(git(root, "rev-parse", "--git-common-dir"))
        common = (root / common).resolve()
        destination = output / root.name
        if common in seen_common or destination in seen_destinations:
            raise ValueError("duplicate repository or worktree destination")
        seen_common.add(common)
        seen_destinations.add(destination)
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"worktree destination already exists: {destination}")
        if output == root or root in output.parents:
            raise ValueError("worktree output must be outside every source checkout")
        if git(root, "status", "--porcelain=v1", "--untracked-files=all"):
            raise ValueError(f"source checkout is not clean: {root}")
        for marker in ("rebase-apply", "rebase-merge", "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "sequencer"):
            marker_path = Path(git(root, "rev-parse", "--git-path", marker))
            if (root / marker_path).exists():
                raise ValueError(f"unfinished Git operation: {root} ({marker})")
        if git(root, "for-each-ref", "--format=%(refname)", f"refs/heads/{branch}"):
            raise ValueError(f"branch already exists: {root} ({branch})")
        head = git(root, "rev-parse", "--verify", "HEAD^{commit}")
        result.append((root, patch, destination, head))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", action="append", nargs=2, required=True,
                        metavar=("CHECKOUT", "MBOX"), help="repeat for each reviewed repository/patch pair")
    parser.add_argument("--branch", required=True, help="new local branch name, shared across target repositories")
    parser.add_argument("--worktree-root", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="apply; otherwise print a read-only plan")
    args = parser.parse_args(argv)
    try:
        output = args.worktree_root.resolve()
        batch = plan(args.target, args.branch, output)
        for root, patch, destination, head in batch:
            print(f"PLAN {root.name}: base={head} patch={patch} worktree={destination}", flush=True)
        if not args.apply:
            return 0
        for root, patch, destination, head in batch:
            if git(root, "rev-parse", "--verify", "HEAD^{commit}") != head:
                raise ValueError(f"source HEAD changed after preflight: {root}")
            output.mkdir(parents=True, exist_ok=True)
            git(root, "worktree", "add", "-b", args.branch, str(destination), head)
            print(f"APPLY {root.name}: git -C {destination} am --3way {patch}", flush=True)
            # Retain failures for inspection. Never --skip, force, or choose ours/theirs.
            git(destination, "am", "--3way", "--", str(patch))
            if git(destination, "status", "--porcelain=v1", "--untracked-files=all"):
                raise ValueError(f"applied worktree is not clean: {destination}")
            after = git(destination, "rev-parse", "HEAD")
            if after == head:
                raise ValueError(f"mailbox created no commits: {destination}")
            print(f"APPLIED {root.name}: {head} -> {after} (local only; tests/CI not implied)", flush=True)
        return 0
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"apply_repo_patches: {exc}", file=sys.stderr)
        print("Stopped. Earlier successful targets remain applied. Inspect the printed worktree; "
              "use git am --abort there only when abandoning a failed application.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
