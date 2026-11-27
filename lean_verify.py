"""Run optional Lean checks with the Python standard library (Python 3.11+)."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile


STANDARD_AXIOMS = frozenset({"propext", "Classical.choice", "Quot.sound"})
THEOREM_NAME = re.compile(r"[A-Za-z_][A-Za-z_0-9']*(?:\.[A-Za-z_][A-Za-z_0-9']*)*")
AXIOMS_MESSAGE = re.compile(r"'(.+)' depends on axioms: \[(.*?)\]", re.DOTALL)
NO_AXIOMS_MESSAGE = re.compile(r"'(.+)' does not depend on any axioms")


def _run(command, *, cwd, timeout, source=None):
    """Capture a process; on POSIX, also terminate its children after a timeout."""
    with subprocess.Popen(
        command, cwd=cwd, stdin=subprocess.PIPE if source is not None else None,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", start_new_session=os.name == "posix",
    ) as process:
        try:
            stdout, stderr = process.communicate(source, timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                process.kill()
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _text(value):
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")


def _diagnostics(stdout, stderr):
    messages = []
    for line in (stdout + "\n" + stderr).splitlines():
        try:
            value = json.loads(line)
        except (ValueError, TypeError):
            continue  # Lake can also print non-JSON build/status messages.
        if isinstance(value, dict) and value.get("severity") in {"information", "warning", "error"}:
            messages.append(value)
    return messages


def _audit(messages, theorems):
    found = {}
    for message in messages:
        if message.get("severity") != "information" or not isinstance(message.get("data"), str):
            continue
        data = message["data"].strip()
        match = AXIOMS_MESSAGE.fullmatch(data)
        if match:
            name, values = match.groups()
            axioms = {value.strip() for value in values.split(",") if value.strip()}
        else:
            match = NO_AXIOMS_MESSAGE.fullmatch(data)
            if not match:
                continue
            name, axioms = match.group(1), set()
        # Union all observations, including any explicitly printed in the source.
        found.setdefault(name, set()).update(axioms)
    missing = [name for name in theorems if name not in found]
    unexpected = {name: sorted(found[name] - STANDARD_AXIOMS)
                  for name in theorems if name in found and found[name] - STANDARD_AXIOMS}
    return ({name: sorted(found[name]) for name in theorems if name in found}, missing, unexpected)


def verify(path, *, lake=False, timeout=120, cwd=None, theorems=()):
    """Check one source snapshot. Relative proof paths are relative to ``cwd``.

    ``theorems`` requests dependency-axiom checks, including imported sorryAx.
    These checks certify the submitted Lean statements, not a Python program.
    """
    working = Path(cwd).resolve() if cwd is not None else Path.cwd()
    proof = (working / Path(path)).resolve()
    names = list(dict.fromkeys(theorems))
    result = dict(path=str(proof), cwd=str(working), status="failed", reason="invalid_input",
                  returncode=None, stdout="", stderr="", command=[], lean_version=None,
                  sha256=None, theorems=names, axioms={},
                  axiom_audit="pending" if names else "not_requested")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        return dict(result, stderr="timeout must be positive and finite")
    if not working.is_dir():
        return dict(result, stderr="working directory not found")
    if any(not isinstance(name, str) or not THEOREM_NAME.fullmatch(name) for name in names):
        return dict(result, stderr="theorem names must be dotted ASCII Lean identifiers")
    if not proof.is_file() or proof.suffix != ".lean":
        return dict(result, reason="proof_not_found", stderr="proof must be an existing .lean file")
    try:
        raw = proof.read_bytes()
        source = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        return dict(result, reason="proof_unreadable", stderr=str(exc))
    result["sha256"] = hashlib.sha256(raw).hexdigest()
    executable = shutil.which("lake" if lake else "lean")
    if executable is None:
        return dict(result, status="unverified", reason="tool_missing",
                    stderr=f"{'lake' if lake else 'lean'} not installed")
    prefix = [executable, "env", "lean"] if lake else [executable]
    try:
        version = _run([*prefix, "--version"], cwd=working, timeout=timeout)
        if version.returncode != 0:
            return dict(result, status="unverified", reason="toolchain_unavailable",
                        returncode=version.returncode, stdout=version.stdout, stderr=version.stderr)
        result["lean_version"] = version.stdout.strip() or version.stderr.strip()
        # --stdin compiles exactly the hashed snapshot and preserves its logical
        # filename/import context without writing generated files into the repo.
        result["command"] = [*prefix, "--json", "-DwarningAsError=true", "--stdin", str(proof)]
        if names:
            source += "\n" + "\n".join(f"#print axioms {name}" for name in names) + "\n"
        run = _run(result["command"], cwd=working, timeout=timeout, source=source)
    except subprocess.TimeoutExpired as exc:
        return dict(result, reason="timeout", stdout=_text(exc.stdout), stderr=_text(exc.stderr),
                    error=f"Lean exceeded {timeout} seconds")
    except FileNotFoundError as exc:
        return dict(result, status="unverified", reason="tool_missing", stderr=str(exc))
    except OSError as exc:
        return dict(result, reason="execution_error", stderr=str(exc))
    result.update(returncode=run.returncode, stdout=run.stdout, stderr=run.stderr)
    messages = _diagnostics(run.stdout, run.stderr)
    result["diagnostics"] = messages
    # Source can override warningAsError. JSON warning diagnostics still fail.
    if run.returncode != 0 or any(m["severity"] in {"error", "warning"} for m in messages):
        return dict(result, reason="proof_failed")
    if names:
        axioms, missing, unexpected = _audit(messages, names)
        result.update(axioms=axioms, missing_theorems=missing, unexpected_axioms=unexpected)
        result["axiom_audit"] = "failed" if missing or unexpected else "passed"
        if missing or unexpected:
            return dict(result, reason="axiom_audit_failed")
    return dict(result, status="passed", reason="verified")


def _write_report(path, report):
    """Replace atomically so CI never uploads a truncated report."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=f".{target.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(report + "\n")
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proofs", nargs="+", type=Path)
    parser.add_argument("--lake", action="store_true", help="run lake env lean in --cwd")
    parser.add_argument("--cwd", type=Path, help="project/toolchain directory; base of relative proof paths")
    parser.add_argument("--required", action="store_true", help="fail if Lean is unavailable")
    parser.add_argument("--timeout", type=float, default=120, help="seconds per subprocess")
    parser.add_argument("--theorem", action="append", default=[],
                        help="audit this theorem's axioms; repeat for one proof entrypoint")
    parser.add_argument("--json-output", type=Path, help="also save report, relative to invocation directory")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("timeout must be positive and finite")
    if args.theorem and len(args.proofs) != 1:
        parser.error("--theorem requires exactly one proof entrypoint")
    results = [verify(p, lake=args.lake, timeout=args.timeout, cwd=args.cwd,
                      theorems=args.theorem) for p in args.proofs]
    status = ("failed" if any(r["status"] == "failed" for r in results)
              else "unverified" if any(r["status"] == "unverified" for r in results) else "passed")
    report = json.dumps({"schema": "lean-verification/1", "status": status,
                         "required": args.required, "results": results}, ensure_ascii=False, indent=2)
    print(report)
    if args.json_output is not None:
        try:
            _write_report(args.json_output, report)
        except OSError as exc:
            print(f"could not save Lean report: {exc}", file=sys.stderr)
            return 1
    return int(status == "failed" or (args.required and status != "passed"))


if __name__ == "__main__":
    sys.exit(main())
