#!/usr/bin/env python3
"""Resolve pilot uv git sources and record the commit uv selected.

``upstream`` tracks browser-test-kit ``main`` on GitHub.
``parent`` tracks this repository's ``main``. Until ``packages/`` is on that
branch, the same ``branch = "main"`` resolution runs against a local git
remote that publishes the commit under test as ``main``.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GITHUB_PARENT = "https://github.com/myon-bioinformatics/myon-bioinformatics"
PARENT_CONTENTS = (
    "https://api.github.com/repos/myon-bioinformatics/myon-bioinformatics"
    "/contents/packages/gh_workflow/pyproject.toml?ref=main"
)
REV = re.compile(r"#([0-9a-f]{40})")

PARENT_IMPORT = """
import pathlib
import gh_ops, gh_workflow, node_launch
identity = pathlib.Path(gh_ops.gh_identity.__file__)
assert identity.name == "gh_identity.py"
assert identity.parent == pathlib.Path(gh_ops.__file__).parent
print("parent-imports-ok")
"""

PARENT_NODE = """
import pathlib, tempfile
import node_launch
script = pathlib.Path(tempfile.mkdtemp()) / "hello.mjs"
script.write_text("process.stdout.write('uv-pilot-ok\\n')\\n", encoding="utf-8")
completed = node_launch.run(script)
assert completed.stdout == b"uv-pilot-ok\\n", (completed.stdout, completed.stderr)
print("parent-node-ok")
"""

UPSTREAM_IMPORT = """
import check_evidence, check_png, jsonl_digest
print("upstream-imports-ok")
"""


def revisions(lock_text: str) -> dict[str, str]:
    """Map package name to the commit recorded on a git source line."""
    found: dict[str, str] = {}
    name = None
    for line in lock_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("name = "):
            name = stripped.split("=", 1)[1].strip().strip('"')
        elif name and stripped.startswith("source = { git ="):
            match = REV.search(stripped)
            if match is None:
                raise SystemExit(f"git source for {name} has no resolved commit")
            found[name] = match.group(1)
            name = None
    return found


def github_parent_packages_published() -> bool:
    request = urllib.request.Request(
        PARENT_CONTENTS,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "uv-git-pilot",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def publish_head_as_main(root: Path) -> str:
    bare = Path(tempfile.mkdtemp(prefix="uv-pilot-bare-")) / "repo.git"
    subprocess.run(["git", "init", "--bare", os.fspath(bare)], check=True)
    subprocess.run(
        ["git", "-C", os.fspath(root), "push", os.fspath(bare), "HEAD:refs/heads/main"],
        check=True,
    )
    return bare.resolve().as_uri()


def materialize_parent_project(root: Path, force_local: bool) -> tuple[Path, str]:
    project = root / "pilot" / "uv-parent"
    if not force_local and github_parent_packages_published():
        return project, "Resolved GitHub branch main."
    bare_uri = publish_head_as_main(root)
    dest = Path(tempfile.mkdtemp(prefix="uv-pilot-parent-"))
    text = (project / "pyproject.toml").read_text(encoding="utf-8")
    if GITHUB_PARENT not in text:
        raise SystemExit("parent pilot pyproject lost its GitHub source URL")
    dest.joinpath("pyproject.toml").write_text(text.replace(GITHUB_PARENT, bare_uri), encoding="utf-8")
    return dest, (
        "GitHub main does not yet contain packages/. "
        "Resolved branch main from a local git remote publishing this commit."
    )


def run_uv(project: Path, exercises: list[str]) -> dict[str, str]:
    subprocess.run(["uv", "lock", "--upgrade"], cwd=project, check=True)
    subprocess.run(["uv", "sync"], cwd=project, check=True)
    for exercise in exercises:
        subprocess.run(["uv", "run", "python", "-c", exercise], cwd=project, check=True)
    found = revisions((project / "uv.lock").read_text(encoding="utf-8"))
    if not found:
        raise SystemExit("uv lock did not record a git commit")
    return found


def write_summary(title: str, found: dict[str, str], note: str) -> None:
    print(title)
    print(note)
    for name, rev in sorted(found.items()):
        print(f"resolved {name} {rev}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    with open(summary, "a", encoding="utf-8") as handle:
        handle.write(f"### {title}\n\n{note}\n\n")
        handle.write("| package | resolved commit |\n| --- | --- |\n")
        for name, rev in sorted(found.items()):
            handle.write(f"| `{name}` | `{rev}` |\n")
        handle.write("\n")


def resolve_upstream() -> dict[str, str]:
    found = run_uv(ROOT / "pilot" / "uv-upstream", [UPSTREAM_IMPORT])
    write_summary("uv upstream pilot", found, "Resolved GitHub branch main.")
    return found


def resolve_parent(root: Path, force_local: bool, exercise_node: bool) -> dict[str, str]:
    project, note = materialize_parent_project(root, force_local)
    exercises = [PARENT_IMPORT]
    if exercise_node:
        exercises.append(PARENT_NODE)
    found = run_uv(project, exercises)
    write_summary("uv parent pilot", found, note)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("which", choices=("upstream", "parent"))
    parser.add_argument("--force-local", action="store_true")
    parser.add_argument("--exercise-node", action="store_true")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    if args.which == "upstream":
        resolve_upstream()
    else:
        resolve_parent(args.root.resolve(), args.force_local, args.exercise_node)
    return 0


if __name__ == "__main__":
    sys.exit(main())
