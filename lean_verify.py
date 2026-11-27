"""Optional Lean verification runner; Python standard library only."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

def verify(path, *, lake=False, timeout=120, cwd=None):
    path = Path(path).resolve()
    result = dict(path=str(path), status="failed", returncode=None, stdout="", stderr="")
    if not path.is_file():
        return dict(result, stderr="proof file not found")
    executable = "lake" if lake else "lean"
    if not shutil.which(executable):
        return dict(result, status="unverified", stderr=f"{executable} not installed")
    command = [executable, "env", "lean"] if lake else [executable]
    try:
        run = subprocess.run([*command, "-DwarningAsError=true", str(path)], cwd=cwd, capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(result, stderr=str(exc))
    # Lean permits sorry by default. Treat warnings as errors using a CLI option.
    return dict(result, status="passed" if run.returncode == 0 else "failed",
                returncode=run.returncode, stdout=run.stdout, stderr=run.stderr)

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proofs", nargs="+", type=Path)
    parser.add_argument("--lake", action="store_true")
    parser.add_argument("--required", action="store_true")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    if not 0 < args.timeout < float("inf"):
        parser.error("timeout must be positive and finite")
    results = [verify(p, lake=args.lake, timeout=args.timeout) for p in args.proofs]
    print(json.dumps({"schema":"lean-verification/1","results":results}, ensure_ascii=False))
    return int(any(r["status"] == "failed" or (args.required and r["status"] != "passed") for r in results))

if __name__ == "__main__":
    sys.exit(main())
