"""Static diagnostics for Python and TOML; user source is never executed."""
import argparse
import json
import tomllib
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    path = args.file
    suffix = path.suffix.lower()
    if suffix not in {".py", ".toml"}:
        parser.error("expected .py or .toml")
    result = {"file": str(path), "valid": True}
    try:
        raw = path.read_bytes()
        if suffix == ".py":
            compile(raw, str(path), "exec", dont_inherit=True)
        else:
            tomllib.loads(raw.decode("utf-8"))
    except (SyntaxError, ValueError, OSError) as error:
        result.update(valid=False, message=str(error), line=getattr(error, "lineno", None),
                      column=getattr(error, "offset", getattr(error, "colno", None)))
    print(json.dumps(result, ensure_ascii=False))
    return int(not result["valid"])


if __name__ == "__main__":
    raise SystemExit(main())
