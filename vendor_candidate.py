"""Test one recommended artifact in a disposable consumer copy (stdlib only)."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import vendor_catalog
import vendor_consumers
import vendor_sync


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_candidate(root, catalog, registry, consumer, repository, source, tests,
                     *, snapshot=None, run=None, timeout=300):
    """Return receipt and JUnit bytes. Never write the original consumer tree.

    Tests are explicit trusted consumer paths, not commands from the catalog.
    This is isolation from accidental writes, not a sandbox for hostile tests.
    """
    root = Path(root).resolve()
    receipt = {"schema": "vendor-candidate-verification/1", "consumer": consumer,
               "repository": repository, "source": source, "status": "unverified",
               "reason": "not_started", "recommended_commit": None,
               "locked": [], "candidate": [], "tests": {"paths": list(tests)},
               "promoted": False, "python": sys.version.split()[0]}
    junit = None
    snapshot = snapshot or vendor_sync._public_git_snapshot
    run = run or subprocess.run
    phase = "inputs"
    try:
        catalog = vendor_catalog.validate(catalog)
        registered = vendor_consumers.get_consumer(registry, consumer)
        receipt["catalog_sha256"] = digest(json.dumps(catalog, sort_keys=True).encode())
        receipt["registry_sha256"] = digest(json.dumps(vendor_consumers.validate(registry), sort_keys=True).encode())
        tool = next((t for t in catalog["tools"] if
                     t["repository"].casefold() == repository.casefold() and t["source"] == source), None)
        if tool is None:
            raise ValueError("artifact has no recommendation")
        receipt["recommended_commit"] = tool["commit"]
        if registered["state"] != "locked":
            receipt["reason"] = "legacy_consumer"
            return receipt, junit
        manifest = vendor_sync._target(root, registered["lock"])
        original = manifest.read_bytes()
        lock = vendor_sync.validate(json.loads(original))
        receipt["locked_manifest_sha256"] = digest(original)
        phase = "locked"
        if not vendor_consumers.compare(registry, lock, consumer)["matches"]:
            raise ValueError("registry/lock topology mismatch")
        phase = "inputs"
        row = next(r for r in vendor_catalog.compare(catalog, lock, consumer)["tools"]
                   if r["repository"].casefold() == repository.casefold() and r["source"] == source)
        receipt["locked"] = row["locked"]
        if row["enrollment"] == "skipped" or not row["locked"]:
            receipt["reason"] = "skipped" if row["enrollment"] == "skipped" else "not_enrolled"
            return receipt, junit
        if not tests:
            receipt["reason"] = "no_tests_selected"
            return receipt, junit
        for test in tests:
            if not test.endswith('.py') or not vendor_sync._target(root, test).is_file():
                raise ValueError("test must be an existing relative Python path")
        phase = "locked"
        vendor_sync.synchronize(registered["lock"], root, "check")
        selected = [i for i in lock["files"] if i["repository"].casefold() == repository.casefold()
                    and i["source"] in (source, "LICENSE")]
        if not any(i["source"] == "LICENSE" for i in selected):
            raise ValueError("explicit locked LICENSE required")
        phase = "fetch"
        resolved, files = snapshot(tool["repository"], tool["commit"], sorted({i["source"] for i in selected}))
        phase = "identity"
        if resolved != tool["commit"]:
            raise ValueError("resolved commit mismatch")
        candidate = copy.deepcopy(lock)
        for item in candidate["files"]:
            if item not in selected:
                continue
            blob, data = files[item["source"]]
            if len(data) > vendor_sync.MAX_BYTES:
                raise ValueError("source exceeds byte limit")
            item.update(commit=resolved, ref=resolved, blob_sha=blob, sha256=digest(data))
            vendor_sync._hex(blob, 40)
            vendor_sync._verify(item, data)
            receipt["candidate"].append(copy.deepcopy(item))
        vendor_sync.validate(candidate)
        phase = "copy"
        with tempfile.TemporaryDirectory(prefix="vendor-candidate-") as directory:
            checkout = Path(directory) / "consumer"
            # Preserve symlinks so canonical path checks can reject vendor links.
            shutil.copytree(root, checkout, symlinks=True,
                            ignore=shutil.ignore_patterns('.git', '__pycache__', '.pytest_cache', 'reports'))
            for item in receipt["candidate"]:
                vendor_sync._target(checkout, item["destination"]).write_bytes(files[item["source"]][1])
            candidate_bytes = (json.dumps(candidate, indent=2) + "\n").encode()
            copied_manifest = vendor_sync._target(checkout, registered["lock"])
            copied_manifest.write_bytes(candidate_bytes)
            vendor_sync.synchronize(registered["lock"], checkout, "check")
            report = Path(directory) / "junit.xml"
            command = [sys.executable, '-m', 'pytest', '-q', *tests, '--junitxml=' + str(report)]
            phase = "tests"
            done = run(command, cwd=checkout, capture_output=True, timeout=timeout)
            receipt["tests"].update(returncode=done.returncode,
                stdout=done.stdout.decode('utf-8', 'replace')[-16000:],
                stderr=done.stderr.decode('utf-8', 'replace')[-16000:])
            if report.is_file():
                junit = report.read_bytes()
                receipt["tests"]["junit_sha256"] = digest(junit)
            # Tests must not invalidate the bytes they purportedly tested.
            phase = "post_test_identity"
            if copied_manifest.read_bytes() != candidate_bytes:
                raise ValueError("tests changed candidate lock")
            vendor_sync.synchronize(registered["lock"], checkout, "check")
            phase = "junit"
            if junit is not None:
                tree = ET.fromstring(junit)
                cases = list(tree.iter('testcase'))
                failures = sum(len(c.findall('failure')) + len(c.findall('error')) for c in cases)
                skipped = sum(len(c.findall('skipped')) for c in cases)
                receipt["tests"].update(executed=len(cases) - skipped, skipped=skipped, failures=failures)
            if done.returncode:
                receipt.update(status="mismatch", reason="tests_failed")
            elif junit is None:
                receipt["reason"] = "missing_junit"
            else:
                if failures or any(int(s.get('failures', 0)) or int(s.get('errors', 0)) for s in tree.iter('testsuite')):
                    receipt.update(status="mismatch", reason="junit_failed")
                elif not cases or skipped:
                    receipt["reason"] = "empty_or_skipped_tests"
                else:
                    receipt.update(status="success", reason="candidate_compatible")
    except (ValueError, OSError, KeyError, ET.ParseError, subprocess.SubprocessError) as error:
        receipt.update(status="mismatch" if phase in ("locked", "identity", "post_test_identity") else "unverified",
                       reason=phase + "_error", error=str(error))
    return receipt, junit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='.')
    parser.add_argument('--catalog', default='vendor-catalog.json')
    parser.add_argument('--registry', default='vendor-consumers.json')
    parser.add_argument('--consumer', required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--test', action='append', default=[])
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    # Reports live outside the input checkout; output reservation precedes work.
    output = Path(args.output).absolute()
    root = Path(args.root).resolve()
    try:
        if output.resolve().is_relative_to(root) or any(p.is_symlink() for p in (output, *output.parents)):
            raise ValueError('output must be outside consumer root, without symlinks')
        output.mkdir(parents=True, exist_ok=False)
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    try:
        catalog = json.loads(Path(args.catalog).read_text(encoding='utf-8'))
        registry = json.loads(Path(args.registry).read_text(encoding='utf-8'))
        receipt, junit = verify_candidate(root, catalog, registry, args.consumer,
                                         args.repository, args.source, args.test)
    except (ValueError, OSError) as error:
        receipt, junit = ({'schema': 'vendor-candidate-verification/1', 'status': 'unverified',
                           'reason': 'input_error', 'error': str(error), 'promoted': False}, None)
    (output / 'verification.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    if junit is not None:
        (output / 'candidate-junit.xml').write_bytes(junit)
    print(json.dumps(receipt, indent=2))
    return 0 if receipt['status'] == 'success' else 2


if __name__ == '__main__':
    raise SystemExit(main())
