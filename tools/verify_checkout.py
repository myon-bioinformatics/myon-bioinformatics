#!/usr/bin/env python3
"""Verify a local Git revision in a disposable detached worktree (stdlib only)."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def _env():
    # Git overrides must not redirect observations or affect the tested checkout.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.pop("PYTHONPATH", None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git(root, *args, ok=(0,)):
    done = subprocess.run(
        ["git", "-C", str(root), "-c", "core.hooksPath=", *args],
        env=_env(), stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=60, check=False)
    if done.returncode not in ok:
        raise ValueError("git %s failed: %s" % (args[0], done.stderr.strip()))
    return done


def verify(root, ref, contains, commands, *, timeout=300):
    """Execute ordered argv lists; return a JSON-safe receipt, stopping on failure.

    Commands are trusted code. No fetch, install, branch switch, push or merge is
    performed. The caller supplies dependencies in the current Python environment.
    A command that changes the worktree HEAD stops the sequence immediately.
    """
    if not isinstance(ref, str) or not ref or ref.startswith("-"):
        raise ValueError("invalid ref")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if not isinstance(contains, (list, tuple)) or any(
            not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", sha)
            for sha in contains):
        raise ValueError("contains requires full commit SHAs")
    if not isinstance(commands, list) or not commands or any(
            not isinstance(argv, list) or not argv or any(
                not isinstance(arg, str) or "\0" in arg for arg in argv) or not argv[0]
            for argv in commands):
        raise ValueError("commands must be a nonempty list of argv lists")
    root = Path(root).resolve()
    # Resolve once: a moving ref cannot change the worktree revision mid-run.
    head = _git(root, "rev-parse", "--verify", ref + "^{commit}").stdout.strip()
    tree = _git(root, "rev-parse", "--verify", head + "^{tree}").stdout.strip()
    report = {"schema": "checkout-verification/1", "ref": ref, "head": head,
              "tree": tree, "contains": list(contains), "ancestry_passed": True,
              "commands": [], "ok": False}
    for ancestor in contains:
        # Missing objects are input errors; unrelated known commits are failures.
        _git(root, "cat-file", "-e", ancestor + "^{commit}")
        if _git(root, "merge-base", "--is-ancestor", ancestor, head, ok=(0, 1)).returncode:
            report["ancestry_passed"] = False
            return report
    with tempfile.TemporaryDirectory(prefix="verify-checkout-") as temporary:
        checkout = Path(temporary) / "checkout"
        added = False
        try:
            _git(root, "worktree", "add", "--detach", str(checkout), head)
            added = True
            for argv in commands:
                argv = [sys.executable if arg == "{python}" else arg for arg in argv]
                row = {"argv": argv}
                try:
                    # Spool logs to disk; publish only the final 8 KiB per stream.
                    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                        try:
                            done = subprocess.run(argv, cwd=checkout, env=_env(),
                                                  stdin=subprocess.DEVNULL, stdout=out,
                                                  stderr=err, timeout=timeout, check=False)
                            row.update(exit=done.returncode,
                                       status="pass" if done.returncode == 0 else "fail")
                        except subprocess.TimeoutExpired:
                            row.update(exit=None, status="timeout")
                        for name, stream in (("stdout", out), ("stderr", err)):
                            size = stream.seek(0, 2)
                            stream.seek(max(0, size - 8192))
                            row[name + "_tail"] = stream.read().decode("utf-8", "replace")
                            row[name + "_truncated"] = size > 8192
                except OSError as exc:
                    row.update(exit=None, status="spawn_error", error=type(exc).__name__)
                report["commands"].append(row)
                if row["status"] != "pass":
                    break
                # Later commands must not run once this one leaves the resolved commit.
                if _git(checkout, "rev-parse", "HEAD").stdout.strip() != head:
                    row["status"] = "head_changed"
                    break
            report["final_head"] = _git(checkout, "rev-parse", "HEAD").stdout.strip()
            report["ok"] = (report["final_head"] == head
                            and len(report["commands"]) == len(commands)
                            and all(row["status"] == "pass" for row in report["commands"]))
        finally:
            if added:
                _git(root, "worktree", "remove", "--force", str(checkout))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--ref", default="origin/main")
    parser.add_argument("--contains", action="append", default=[], metavar="SHA")
    parser.add_argument("--command", action="append", required=True, metavar="JSON_ARGV")
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args(argv)
    try:
        report = verify(args.root, args.ref, args.contains,
                        [json.loads(value) for value in args.command], timeout=args.timeout)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
