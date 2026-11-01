#!/usr/bin/env python3
"""Copy only already-installed, receipt-matching test files; no install/network."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path


def stage(site_packages: Path, destination: Path, receipt: Path):
    source = site_packages.resolve(strict=True)
    rows = json.loads(receipt.read_text())['files']
    checked = []
    for row in rows:
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('unsafe receipt path')
        path = (source / relative).resolve(strict=True)
        if not path.is_relative_to(source) or not path.is_file():
            raise ValueError('test-tool source escaped site-packages')
        data = path.read_bytes()
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('test-tool bytes differ: ' + row['path'])
        checked.append((relative, data))
    destination.mkdir(parents=True, exist_ok=False)
    for relative, data in checked:
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return {'files': len(checked), 'bytes': sum(len(data) for _, data in checked), 'network_or_installs': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--site-packages', required=True, type=Path)
    p.add_argument('--destination', default=Path('test-tools'), type=Path)
    p.add_argument('--receipt', default=Path('test-tools-receipt.json'), type=Path)
    args = p.parse_args()
    print(json.dumps(stage(args.site_packages, args.destination, args.receipt)))
