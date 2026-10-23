#!/usr/bin/env python3
"""Read-only prerequisites using adopted ghi and optional yourself runtime facts."""
import argparse
import json
from pathlib import Path
import re
import sys

from vendor import gh_identity as ghi


def runtime_facts(path):
    """Accept collect() JSON, retaining only OS/runtime facts (no host/directory)."""
    with path.open('rb') as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError('runtime facts exceed 64 KiB')
    try:
        data = json.loads(raw)
    except RecursionError as error:
        raise ValueError('runtime facts nesting is too deep') from error
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        raise ValueError('expected yourself.collect() schema_version 1')
    result = {}
    for group, fields in (('os', ('system', 'release', 'machine')),
                          ('runtime', ('implementation', 'version', 'bits'))):
        source = data.get(group)
        if not isinstance(source, dict):
            raise ValueError('missing runtime facts group: ' + group)
        values = {}
        for field in fields:
            value = source.get(field)
            if field == 'bits':
                valid = type(value) is int and value in (32, 64)
            else:
                valid = isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 for c in value)
            if not valid:
                raise ValueError('invalid runtime fact: ' + group + '.' + field)
            values[field] = value
        result[group] = values
    return result


def observe(root, *, required=(), python_min=None, require_clean=False,
            require_token=False, reported_runtime=None):
    if not root.is_dir():
        raise ValueError('root must be a directory')
    capabilities = ghi.capabilities()
    status = ghi.git_status(root)
    checks = [{'check': name, 'ok': capabilities[name]} for name in dict.fromkeys(required)]
    if python_min:
        need = tuple(int(x) for x in python_min.split('.'))
        checks.append({'check': 'python_min', 'required': python_min,
                       'ok': sys.version_info[:len(need)] >= need})
    if require_clean:
        checks.append({'check': 'clean_worktree', 'ok': status['clean'] and not status['truncated']})
    if require_token:
        checks.append({'check': 'token_present', 'ok': capabilities['token_present']})
    return {'schema': 'repository-preflight/1', 'capabilities': capabilities,
            'worktree': {'clean': status['clean'], 'truncated': status['truncated'],
                         'changed_records': len(status['records'])},
            'checks': checks, 'ready': all(row['ok'] for row in checks),
            'reported_runtime': ({'source': 'supplied_yourself_collect_json',
                                  'freshness': 'not_verified', 'facts': reported_runtime}
                                 if reported_runtime is not None else None)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--require', action='append', choices=('git', 'gh'), default=[])
    parser.add_argument('--python-min', help='minimum current Python version, e.g. 3.11')
    parser.add_argument('--require-clean', action='store_true')
    parser.add_argument('--require-token', action='store_true', help='presence only, not authentication or permissions')
    parser.add_argument('--runtime-facts', type=Path, help='JSON emitted by yourself.collect(); never executes supplied code')
    parser.add_argument('--explain', action='store_true',
                        help='on failure, append a guiding message; git inspection errors still omit local paths')
    parser.epilog = ('Exit codes: 0 ready, 1 a check failed, 2 input or git inspection error. '
                     'Stdout is JSON on success. Without --explain, stderr is only the exception type '
                     'so local paths are not printed.')
    args = parser.parse_args(argv)
    if args.python_min and not re.fullmatch(r'[0-9]+(?:\.[0-9]+){0,2}', args.python_min):
        parser.error('--python-min requires one to three numeric components')
    try:
        facts = runtime_facts(args.runtime_facts) if args.runtime_facts else None
        result = observe(args.root, required=args.require, python_min=args.python_min,
                         require_clean=args.require_clean, require_token=args.require_token,
                         reported_runtime=facts)
    except (ValueError, OSError, ghi.GitInspectionError) as error:
        # Git errors may contain local paths; the default exposes the category, not stderr.
        if not args.explain:
            print('preflight: ' + type(error).__name__, file=sys.stderr)
        elif isinstance(error, ghi.GitInspectionError):
            print('preflight: GitInspectionError: local paths omitted; run git status in --root',
                  file=sys.stderr)
        else:
            print('preflight: ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if result['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
