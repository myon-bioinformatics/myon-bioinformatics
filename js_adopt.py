"""Adopt a JS file from a public GitHub repository: pinned raw URL, sha256, SRI and license in one lock (js-adopt).

    python -S js_adopt.py add cure53/DOMPurify 3.4.17 dist/purify.min.js --dest vendor/js --license "MPL-2.0 OR Apache-2.0"
    python -S js_adopt.py check                      # every adopted file still matches its sha256 / SRI
    python -S js_adopt.py tag purify                 # <script src="vendor/js/..." integrity="sha384-..."> (local)
    python -S js_adopt.py tag purify --cdn           # the same file from jsDelivr's GitHub mirror, same integrity
    python -S js_adopt.py oneliners purify           # equivalent curl / browser fetch / Python urllib commands

`add` downloads https://raw.githubusercontent.com/OWNER/REPO/REF/PATH with urllib (no git, no npm),
writes it under --dest, and records `js-adopt/1` in js.lock.json: repository, ref, path, url,
destination, bytes, sha256, the browser SRI value (sha384), and the SPDX license you state. A tag
can move, so the sha256 is what pins the bytes; `add` also marks the file `-text` in
.gitattributes so Windows checkouts keep the exact bytes. `check` re-hashes the local copy, and
`check --remote` re-downloads to detect a moved ref. The SRI value lets a page refuse a changed
file (`<script integrity>` or `fetch(url, {integrity})`) whether it loads the local copy or the CDN.

Stdlib only; runs under `python -S`. Exit codes: 0 OK; 1 mismatch or license not allowed; 2 input error.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import quote
from urllib.request import Request, urlopen

SCHEMA = "js-adopt/1"
LOCK = "js.lock.json"
TIMEOUT_SECONDS = 30  # fetch-guard: every download ends within a deadline
MAX_BYTES = 10_000_000
# Licenses that may be shipped without review; anything else needs --allow-license.
ALLOWED = {"MIT", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "MPL-2.0", "0BSD", "Unlicense", "CC0-1.0"}
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]*")


class LicenseNotAllowed(Exception):
    """The stated SPDX license is outside the allow list and was not reviewed."""


def raw_url(repository: str, ref: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{repository}/{quote(ref, safe='')}/{quote(path, safe='/')}"


def cdn_url(entry: dict) -> str:
    return f"https://cdn.jsdelivr.net/gh/{entry['repository']}@{quote(entry['ref'], safe='')}/{quote(entry['path'], safe='/')}"


def sri(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()


# A separate process makes the deadline cover DNS, headers and a trickling body.
# No bytes are published until the response is complete and within the cap.
_URLLIB_READ = """import sys, urllib.request
url, cap, seconds = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "js-adopt/1"}), timeout=seconds) as response:
    length = response.headers.get("Content-Length")
    if length is not None and int(length) > cap:
        raise ValueError("download exceeds byte limit")
    data = response.read(cap + 1)
    if len(data) > cap:
        raise ValueError("download exceeds byte limit")
    if length is not None and len(data) != int(length):
        raise ValueError("incomplete response")
sys.stdout.buffer.write(data)
"""


def download(url: str) -> bytes:
    try:
        result = subprocess.run(
            [sys.executable, "-S", "-c", _URLLIB_READ, url, str(MAX_BYTES), str(TIMEOUT_SECONDS)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT_SECONDS, check=True)
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise ValueError("download failed, exceeded byte limit or total deadline") from exc
    return result.stdout


def license_ok(expression: str, *, strict: bool = False) -> bool:
    """Parse ID / parentheses / AND / OR, with AND binding tighter than OR.

    Parse every branch even when its value cannot change the answer. WITH,
    LicenseRef, legacy '+' and malformed/overly complex expressions fail closed.
    """
    if not isinstance(expression, str) or len(expression) > 4096:
        if strict:
            raise ValueError("unsupported SPDX expression")
        return False
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9.-]*|[()]|[^\s]", expression)
    if not tokens or len(tokens) > 256:
        if strict:
            raise ValueError("unsupported SPDX expression")
        return False
    position = 0

    def primary():
        nonlocal position
        if position >= len(tokens):
            raise ValueError("missing operand")
        token = tokens[position]
        position += 1
        if token == "(":
            value = disjunction()
            if position >= len(tokens) or tokens[position] != ")":
                raise ValueError("unclosed parenthesis")
            position += 1
            return value
        if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", token)
                or token in {"AND", "OR", "WITH"} or token.startswith(("LicenseRef-", "DocumentRef-"))):
            raise ValueError("unsupported operand")
        return token in ALLOWED

    def conjunction():
        nonlocal position
        value = primary()
        while position < len(tokens) and tokens[position] == "AND":
            position += 1
            right = primary()
            value = value and right
        return value

    def disjunction():
        nonlocal position
        value = conjunction()
        while position < len(tokens) and tokens[position] == "OR":
            position += 1
            right = conjunction()
            value = value or right
        return value

    try:
        value = disjunction()
        if position != len(tokens):
            raise ValueError("trailing SPDX tokens")
        return value
    except (ValueError, RecursionError) as exc:
        if strict:
            raise ValueError("unsupported SPDX expression") from exc
        return False


def _safe_relative(value: str, what: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or "\\" in value or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def load_lock(path: Path) -> dict:
    if not path.exists():
        return {"schema": SCHEMA, "files": []}
    lock = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(lock, dict) or lock.get("schema") != SCHEMA or not isinstance(lock.get("files"), list):
        raise ValueError(f"expected {SCHEMA} in {path}")
    return lock


def save_lock(path: Path, lock: dict) -> None:
    lock["files"].sort(key=lambda e: e["name"])
    path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _gitattributes_covers(root: Path, destination: str) -> bool:
    """True when .gitattributes marks the file -text, exactly or through a `dir/**` pattern."""
    path = root / ".gitattributes"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    for line in lines:
        parts = line.split()
        if len(parts) >= 2 and "-text" in parts[1:]:
            pattern = parts[0]
            if pattern == destination or (pattern.endswith("/**") and destination.startswith(pattern[:-2])):
                return True
    return False


def ensure_gitattributes(root: Path, destination: str) -> bool:
    """Keep adopted bytes exact: Windows checkouts (core.autocrlf) would rewrite LF to CRLF and
    break the SRI hash in every engine (CRLF_BREAKS_SRI). Returns True when a line was added."""
    if _gitattributes_covers(root, destination):
        return False
    path = root / ".gitattributes"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.write_text(text + ("" if not text or text.endswith("\n") else "\n") + f"{destination} -text\n",
                    encoding="utf-8")
    return True


def add(lock: dict, root: Path, repository: str, ref: str, path: str, dest: str, *, license: str,
        name: str | None = None, allow_license: bool = False, fetch=download) -> dict:
    if not _REPO.fullmatch(repository) or any(p in (".", "..") for p in repository.split("/")):
        raise ValueError("expected OWNER/REPO")
    if not ref or ref.startswith("-") or ".." in ref or re.search(r"\s", ref):
        raise ValueError("invalid ref")
    _safe_relative(path, "path")
    _safe_relative(dest, "destination directory")
    name = name or PurePosixPath(path).name.split(".")[0].lower()
    if not _NAME.fullmatch(name):
        raise ValueError(f"invalid name: {name!r}")
    if not license_ok(license, strict=True) and not allow_license:
        raise LicenseNotAllowed(f"license {license!r} is not in the allow list; review it and pass --allow-license")
    url = raw_url(repository, ref, path)
    data = fetch(url)
    destination = f"{dest.rstrip('/')}/{PurePosixPath(path).name}"
    target = root / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    entry = {"name": name, "repository": repository, "ref": ref, "path": path, "url": url,
             "destination": destination, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
             "sri": sri(data), "license": license}
    lock["files"] = [e for e in lock["files"] if e["name"] != name and e["destination"] != destination] + [entry]
    ensure_gitattributes(root, destination)
    return entry


def check(lock: dict, root: Path, *, remote: bool = False, fetch=download) -> list[str]:
    problems = []
    for e in lock["files"]:
        target = root / _safe_relative(e["destination"], "destination")
        data = target.read_bytes() if target.is_file() else None
        if data is None:
            problems.append(f"{e['name']}: missing {e['destination']}")
        elif hashlib.sha256(data).hexdigest() != e["sha256"] or sri(data) != e["sri"]:
            problems.append(f"{e['name']}: local bytes differ from the lock")
        if not _gitattributes_covers(root, e["destination"]):
            problems.append(f"{e['name']}: .gitattributes lacks '{e['destination']} -text' (CRLF would break SRI)")
        if not license_ok(e["license"], strict=True) and not e.get("license_reviewed"):
            problems.append(f"{e['name']}: license {e['license']} not allowed")
        if remote and hashlib.sha256(fetch(e["url"])).hexdigest() != e["sha256"]:
            problems.append(f"{e['name']}: {e['ref']} now serves different bytes (moved tag?)")
    return problems


def find(lock: dict, name: str) -> dict:
    for e in lock["files"]:
        if e["name"] == name:
            return e
    raise ValueError(f"not adopted: {name}")


def script_tag(entry: dict, *, cdn: bool = False, base: str = "") -> str:
    src = cdn_url(entry) if cdn else base + entry["destination"]
    return f'<script src="{src}" integrity="{entry["sri"]}" crossorigin="anonymous"></script>'


# This supervisor bounds stdout even when curl receives no Content-Length.
# A timer kills the actual network process, including during DNS/blocked reads.
_FETCH_EXAMPLE = """import hashlib, os, subprocess, sys, tempfile, threading
command = COMMAND
process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
timer = threading.Timer(SECONDS, process.kill)
timer.start()
try:
    data = process.stdout.read(CAP + 1)
    if len(data) > CAP:
        raise ValueError("download exceeds byte limit")
    if process.wait() != 0:
        raise ValueError("download failed or total deadline exceeded")
finally:
    timer.cancel()
    if process.poll() is None:
        process.kill()
    process.wait()
    process.stdout.close()
if hashlib.sha256(data).hexdigest() != DIGEST:
    raise ValueError("sha256 mismatch")
name = None
try:
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(DEST) or ".", delete=False) as target:
        name = target.name
        target.write(data)
    os.replace(name, DEST)
finally:
    if name is not None and os.path.exists(name):
        os.unlink(name)
"""


def oneliners(entry: dict) -> list[str]:
    """Standalone POSIX shell commands and a browser-console expression.

    The shell examples need Python 3; curl additionally needs curl. Destinations
    are replaced only after successful completion and hash verification.
    """
    def shell(command):
        values = dict(COMMAND=command, SECONDS=repr(TIMEOUT_SECONDS), CAP=repr(MAX_BYTES),
                      DIGEST=repr(entry["sha256"]), DEST=repr(entry["destination"]))
        code = re.sub(r"\b(COMMAND|SECONDS|CAP|DIGEST|DEST)\b", lambda m: values[m[0]], _FETCH_EXAMPLE)
        return "python -S -c " + shlex.quote(code)

    curl = shell(repr(["curl", "-fsSL", "--max-time", str(TIMEOUT_SECONDS),
                       "--max-filesize", str(MAX_BYTES), entry["url"]]))
    urllib = shell("[sys.executable, '-S', '-c', " + repr(_URLLIB_READ) + ", "
                   + repr(entry["url"]) + ", " + repr(str(MAX_BYTES)) + ", "
                   + repr(str(TIMEOUT_SECONDS)) + "]")
    # fetch's integrity option may buffer the entire response before exposing a
    # stream. Verify SRI ourselves after the bounded read instead.
    browser = """await (async () => {
const controller = new AbortController();
let reader, timer;
const work = (async () => {
  const response = await fetch(URL, {signal: controller.signal});
  if (!response.ok) throw new Error('HTTP ' + response.status);
  const length = response.headers.get('content-length');
  if (length !== null && Number(length) > CAP) throw new Error('byte limit');
  reader = response.body.getReader();
  const chunks = []; let size = 0;
  for (;;) {
    const {done, value} = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > CAP) throw new Error('byte limit');
    chunks.push(value);
  }
  const data = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { data.set(chunk, offset); offset += chunk.byteLength; }
  const hash = new Uint8Array(await crypto.subtle.digest('SHA-384', data));
  const integrity = 'sha384-' + btoa(String.fromCharCode(...hash));
  if (integrity !== SRI) throw new Error('integrity mismatch');
  return new TextDecoder().decode(data);
})();
try {
  return await Promise.race([work, new Promise((_, reject) => {
    timer = setTimeout(() => { controller.abort(); reject(new Error('total deadline')); }, MILLISECONDS);
  })]);
} finally {
  clearTimeout(timer); controller.abort();
  if (reader) { reader.cancel().catch(() => {}); }
}
})()"""
    values = dict(URL=json.dumps(cdn_url(entry)), CAP=str(MAX_BYTES),
                  SRI=json.dumps(entry["sri"]), MILLISECONDS=str(TIMEOUT_SECONDS * 1000))
    browser = re.sub(r"\b(URL|CAP|SRI|MILLISECONDS)\b", lambda m: values[m[0]], browser)
    return [curl, browser, urllib]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", default=LOCK)
    parser.add_argument("--root", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("add", help="download one pinned file and record it")
    a.add_argument("repository", metavar="OWNER/REPO")
    a.add_argument("ref", help="tag or full commit SHA")
    a.add_argument("path", help="file path inside the repository")
    a.add_argument("--dest", required=True, help="directory to write into, relative to --root")
    a.add_argument("--license", required=True, help="SPDX expression, e.g. MIT or \"MPL-2.0 OR Apache-2.0\"")
    a.add_argument("--name", help="short name for tag/oneliners (default: file stem)")
    a.add_argument("--allow-license", action="store_true", help="record a reviewed license outside the allow list")
    c = sub.add_parser("check", help="verify local bytes, SRI and licenses")
    c.add_argument("--remote", action="store_true", help="also re-download to detect a moved ref")
    t = sub.add_parser("tag", help="print a <script> tag with integrity")
    t.add_argument("name")
    t.add_argument("--cdn", action="store_true")
    t.add_argument("--base", default="", help="prefix for the local src, e.g. ../")
    o = sub.add_parser("oneliners", help="equivalent curl / browser fetch / urllib commands")
    o.add_argument("name")
    args = parser.parse_args(argv)
    root, lock_path = Path(args.root), Path(args.root) / args.lock
    try:
        lock = load_lock(lock_path)
        if args.command == "add":
            entry = add(lock, root, args.repository, args.ref, args.path, args.dest, license=args.license,
                        name=args.name, allow_license=args.allow_license)
            if args.allow_license and not license_ok(entry["license"]):
                entry["license_reviewed"] = True
            save_lock(lock_path, lock)
            print(f"{entry['name']} {entry['repository']}@{entry['ref']} {entry['destination']} "
                  f"{entry['bytes']} bytes sha256={entry['sha256']}")
            print(script_tag(entry))
            return 0
        if args.command == "check":
            problems = check(lock, root, remote=args.remote)
            for line in problems:
                print(line)
            print(f"{len(lock['files'])} adopted, {len(problems)} problem(s)")
            return 1 if problems else 0
        entry = find(lock, args.name)
        print(script_tag(entry, cdn=args.cdn, base=args.base) if args.command == "tag" else "\n".join(oneliners(entry)))
        return 0
    except LicenseNotAllowed as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
