#!/usr/bin/env python3
"""GitHub Actions の uses を、最新リリースの full SHA 固定の方針と照合する。

検査対象: .github/workflows/*.yml、*.yaml と templates/*.workflow.yml。

方針（2026-10-31 myon さんの判断）:

- ロック採用 workflow は actions.lock の完全バージョンタグ・commit・IDを検証する。
- 未採用 workflow / templates の外部の action（actions/*、astral-sh/setup-uv、サードパーティを含むすべて）は、
  そのリリースの 40 桁 commit SHA と行末の `# vX.Y.Z`。
- 同じリポジトリの action / reusable workflow は相対パス（./）。docker:// はそのまま。
- 親 myon-bioinformatics/myon-bioinformatics の reusable workflow は
  .github/workflows/*.yml@40桁SHA（コメントは自由）。

--check-hashes は、そのコメントのリリースタグが指す commit と SHA が
一致するかを見る。--warn のとき不一致は警告で終了 0。既定は終了 1。
API 自体が失敗したときは --warn でも終了 2。

標準ライブラリのみの書式検査で、最新リリースの探索は行わない。
uses は block mapping（キーの単純な引用を含む）で記述する。
flow mapping、空や複数行の uses は未対応として拒否する。
run 等の block scalar 内の文字列は action として扱わない。
"""
from __future__ import annotations

import argparse, json, os, re, sys, urllib.error, urllib.request
from pathlib import Path; from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
PATCH = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
USES_KEY = r"(?:uses|'uses'|\"uses\")"
USES_LINE = re.compile(r"^[ \t]*(?:-[ \t]*)?" + USES_KEY + r"[ \t]*:[ \t]*(?P<value>.*?)\s*$")
FLOW_USES = re.compile(r"[,{][ \t]*" + USES_KEY + r"[ \t]*:")
UNSUPPORTED_FLOW = "<unsupported flow mapping uses>"
BLOCK_SCALAR = re.compile(r":[ \t]*[>|][0-9+-]*[ \t]*(?:#.*)?$")
PARENT_PREFIX = "myon-bioinformatics/myon-bioinformatics/"


class HashCheckError(RuntimeError):
    """GitHub のタグ解決に失敗した。"""


def _split_uses(value: str) -> tuple[str, str]:
    text = value.strip()
    if text[:1] in {"'", '"'}:
        quote_char = text[0]
        end = text.find(quote_char, 1)
        if end == -1:
            return text, ""
        raw = text[1:end].strip()
        rest = text[end + 1 :].strip()
        if rest.startswith("#"):
            return raw, rest[1:].strip()
        return raw, ""
    if " #" in text:
        raw, comment = text.split(" #", 1)
        return raw.strip(), comment.strip()
    return text, ""


def _has_flow_uses(line: str) -> bool:
    """Find flow keys, ignoring quoted scalar contents and YAML comments."""
    # Ordinary block scalar values (names, shell commands) are not flow maps.
    start = line.lstrip().removeprefix("-").lstrip()
    if not start.startswith(("{", "[")):
        _, separator, value = start.partition(":")
        if not separator or not value.lstrip().startswith(("{", "[")):
            return False
    masked = []
    index = 0
    while index < len(line):
        char = line[index]
        if char == "#" and (index == 0 or line[index - 1].isspace()):
            break
        if char not in {"'", '"'}:
            masked.append(char)
            index += 1
            continue
        begin = index
        index += 1
        while index < len(line):
            if char == '"' and line[index] == "\\":
                index += 2
                continue
            if line[index] == char:
                if char == "'" and line[index:index + 2] == "''":
                    index += 2
                    continue
                index += 1
                break
            index += 1
        quoted = line[begin:index]
        is_key = quoted in {"'uses'", '"uses"'} and line[index:].lstrip().startswith(":")
        masked.append(quoted if is_key else " " * len(quoted))
    return FLOW_USES.search("".join(masked)) is not None


def _iter_uses(root: Path):
    workflow_dir = root / ".github" / "workflows"
    paths = sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))
    # 子に配る workflow テンプレートも同じ方針で検査する。
    paths += sorted((root / "templates").glob("*.workflow.yml"))
    for path in paths:
        lines = path.read_text(encoding="utf-8").splitlines()
        scalar_indent = None
        for lineno, line in enumerate(lines, 1):
            indent = len(line) - len(line.lstrip())
            if scalar_indent is not None:
                if not line.strip() or indent > scalar_indent:
                    continue
                scalar_indent = None
            if line.lstrip().startswith("#"):
                continue
            if BLOCK_SCALAR.search(line) and USES_LINE.match(line) is None:
                # A sequence item's '-' is outside its mapping. The following
                # sibling keys align with the key after '- ', not with the dash.
                prefix = re.match(r"^[ \t]*(?:-[ \t]+)?", line)
                scalar_indent = prefix.end()
                continue
            # This stdlib scanner supports block mappings, not a YAML parser.
            # Reject flow-style uses explicitly rather than silently approving it.
            if _has_flow_uses(line):
                yield path, lineno, UNSUPPORTED_FLOW, ""
                continue
            match = USES_LINE.match(line)
            if match is None:
                continue
            raw, comment = _split_uses(match.group("value"))
            yield path, lineno, raw, comment


def _needs_sha(action: str) -> bool:
    return not action.startswith(("./", "docker://", PARENT_PREFIX))


def _style_problem(raw: str, comment: str) -> str | None:
    if raw == UNSUPPORTED_FLOW:
        return "uses の flow mapping は未対応。block mapping で記述してください"
    if raw.startswith("./") or raw.startswith("docker://"):
        return None
    if "@" not in raw:
        return "ref が無い"
    action, rev = raw.rsplit("@", 1)
    if action.startswith(PARENT_PREFIX):
        entry = action[len(PARENT_PREFIX) :]
        if (
            entry.startswith(".github/workflows/")
            and entry.endswith((".yml", ".yaml"))
            and HEX40.fullmatch(rev)
        ):
            return None
        return "親の reusable workflow は .github/workflows/*.yml@40桁SHA"
    if _needs_sha(action):
        if not HEX40.fullmatch(rev) or not PATCH.fullmatch(comment):
            return "action は 40 桁 SHA と # vX.Y.Z"
        return None
    return None


def action_lock(root: Path) -> dict:
    """Read the official generator's v0.0.3 YAML subset; reject other syntax."""
    path = root / '.github/workflows/actions.lock'
    if not path.exists():
        return {'workflows': {}, 'dependencies': {}}
    data = {}
    section = None
    entry = None
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        def bad():
            raise ValueError(f'actions.lock:{number}: unsupported or duplicate lockfile entry')
        if line == "version: 'v0.0.3'":
            if 'version' in data:
                bad()
            data['version'] = 'v0.0.3'
        elif line in ('workflows:', 'dependencies:'):
            section = line[:-1]
            if section in data:
                bad()
            data[section] = {}
            entry = None
        elif section and (match := re.fullmatch(r"    '([^'\\]+)':", line)):
            entry = match[1]
            if entry in data[section]:
                bad()
            data[section][entry] = [] if section == 'workflows' else {}
        elif section == 'workflows' and entry and (match := re.fullmatch(r"        - '([^'\\]+)'", line)):
            if match[1] in data[section][entry]:
                bad()
            data[section][entry].append(match[1])
        elif section == 'dependencies' and entry and (match := re.fullmatch(r"        (ref|commit|owner_id|repo_id): (?:'([^'\\]+)'|([0-9]+))", line)):
            field, string, integer = match.groups()
            if field in data[section][entry]:
                bad()
            data[section][entry][field] = int(integer) if integer else string
        else:
            bad()
    if set(data) != {'version', 'workflows', 'dependencies'} or not data.get('workflows'):
        raise ValueError('actions.lock: incomplete lockfile')
    for workflow, refs in data['workflows'].items():
        if (not re.fullmatch(r'\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml', workflow)
                or not (root / workflow).is_file() or not refs):
            raise ValueError('actions.lock: invalid workflow path or empty dependencies')
        if any(ref not in data['dependencies'] for ref in refs):
            raise ValueError('actions.lock: workflow dependency missing')
    for ref, record in data['dependencies'].items():
        action, sep, tag = ref.rpartition('@')
        if (not sep or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', action)
                or '.github/workflows/' in action or not PATCH.fullmatch(tag)
                or set(record) != {'ref', 'commit', 'owner_id', 'repo_id'}
                or record['ref'] != tag or not isinstance(record['commit'], str)
                or not re.fullmatch(r'sha1-[0-9a-f]{40}', record['commit'])
                or any(type(record[k]) is not int or record[k] <= 0 for k in ('owner_id', 'repo_id'))):
            raise ValueError('actions.lock: invalid dependency record')
    return data


def _locked(root, lock, path, raw):
    rel = path.relative_to(root).as_posix()
    return (raw in lock['workflows'].get(rel, [])
            and raw in lock['dependencies'])


def github_action_identity(repository):
    data = _github_json(f'https://api.github.com/repos/{repository}')
    owner = data.get('owner', {})
    if type(data.get('id')) is not int or type(owner.get('id')) is not int:
        raise HashCheckError(f'{repository}: invalid repository identity')
    return owner['id'], data['id']


def style_problems(root: Path) -> list[str]:
    found: list[str] = []
    try:
        lock = action_lock(root)
    except (ValueError, OSError) as exc:
        return [str(exc)]
    for path, lineno, raw, comment in _iter_uses(root):
        if _locked(root, lock, path, raw):
            continue
        rel = path.relative_to(root).as_posix()
        if (rel in lock['workflows'] and '@' in raw
                and not raw.startswith(('./', 'docker://'))
                and '.github/workflows/' not in raw):
            found.append(f'{rel}:{lineno}: action missing from workflow lock ({raw})')
            continue
        problem = _style_problem(raw, comment)
        if problem:
            rel = path.relative_to(root).as_posix()
            found.append(f"{rel}:{lineno}: {problem} ({raw})")
    return found


def _github_json(url: str) -> dict:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "parent-action-uses",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise HashCheckError(f"GitHub API HTTP {exc.code} for {url}") from exc
    except urllib.error.URLError as exc:
        raise HashCheckError(f"GitHub API に到達できない: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise HashCheckError(f"GitHub API の応答が object でない: {url}")
    return payload


def github_tag_commit(repository: str, tag: str) -> str:
    """リリースタグが指す commit SHA。注釈タグは中の commit まで辿る。"""
    data = _github_json(
        f"https://api.github.com/repos/{repository}/git/ref/tags/{quote(tag, safe='')}"
    )
    obj = data.get("object")
    if not isinstance(obj, dict):
        raise HashCheckError(f"{repository}@{tag} の object が無い")
    kind = obj.get("type")
    sha = obj.get("sha")
    if kind == "commit" and isinstance(sha, str) and HEX40.fullmatch(sha):
        return sha.lower()
    if kind == "tag" and isinstance(obj.get("url"), str):
        tagged = _github_json(obj["url"])
        inner = tagged.get("object")
        if isinstance(inner, dict) and inner.get("type") == "commit":
            inner_sha = inner.get("sha")
            if isinstance(inner_sha, str) and HEX40.fullmatch(inner_sha):
                return inner_sha.lower()
    raise HashCheckError(f"{repository}@{tag} の commit を解決できない")


def hash_problems(root: Path, resolve=github_tag_commit, identity=github_action_identity) -> list[str]:
    """action の SHA が、コメントのリリースタグの commit と一致するか。"""
    found: list[str] = []
    resolved: dict[tuple[str, str], str] = {}
    try:
        lock = action_lock(root)
    except (ValueError, OSError) as exc:
        raise HashCheckError(str(exc)) from exc
    identities = {}
    for ref, record in lock['dependencies'].items():
        repository = '/'.join(ref.split('@')[0].split('/')[:2])
        tag = record['ref']
        key = (repository, tag)
        if key not in resolved:
            resolved[key] = resolve(repository, tag).lower()
        if resolved[key] != record['commit'][5:]:
            raise HashCheckError(f'actions.lock: {ref} release commit mismatch')
        if repository not in identities:
            identities[repository] = identity(repository)
        if identities[repository] != (record['owner_id'], record['repo_id']):
            raise HashCheckError(f'actions.lock: {ref} repository identity mismatch')
    for path, lineno, raw, comment in _iter_uses(root):
        if "@" not in raw:
            continue
        action, rev = raw.rsplit("@", 1)
        if not _needs_sha(action):
            continue
        if not HEX40.fullmatch(rev) or not PATCH.fullmatch(comment):
            continue
        # A subaction such as actions/cache/restore shares its repository tag.
        repository = "/".join(action.split("/")[:2])
        key = (repository, comment)
        if key not in resolved:
            resolved[key] = resolve(repository, comment).lower()
        if resolved[key] != rev.lower():
            rel = path.relative_to(root).as_posix()
            found.append(
                f"{rel}:{lineno}: {action}@{rev} は {comment} の commit {resolved[key]} と違う"
            )
    return found


def main(argv: list[str] | None = None, resolve=github_tag_commit, identity=github_action_identity) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--warn", action="store_true", help="不一致を警告にして終了コード 0")
    parser.add_argument(
        "--check-hashes",
        action="store_true",
        help="action の SHA をリリースタグの commit と照合する",
    )
    parser.add_argument("--list-locked-workflows", action="store_true", help="print validated onboarded workflow paths")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.list_locked_workflows:
        try:
            print('\n'.join(action_lock(root)['workflows']))
        except (ValueError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
        return 0
    found = style_problems(root)
    hash_error = None
    if args.check_hashes:
        try:
            found.extend(hash_problems(root, resolve, identity))
        except HashCheckError as exc:
            hash_error = exc
    if hash_error is not None:
        for item in found:
            print(f"::warning::{item}" if args.warn else item, file=sys.stderr)
        print(f"::error::{hash_error}", file=sys.stderr)
        return 2
    if not found:
        print("action uses ok")
        return 0
    for item in found:
        if args.warn:
            print(f"::warning::{item}", file=sys.stderr)
        else:
            print(item, file=sys.stderr)
    if args.warn:
        print("action uses の不一致は pull request では警告", file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
