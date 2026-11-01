#!/usr/bin/env python3
"""Prepare the pinned Ironmate PPTX source slice with npm, offline; never install.

This is a fixed snapshot contract, not a general npm dependency resolver. npm
projects the existing lock; the result must retain exactly the reviewed records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

REPOSITORY = "myon-bioinformatics/Ironmate"
COMMIT = "af846ee7b1fd440b8379e2a682dfc7d40365b30e"
MAX_INPUT_BYTES = 2 * 1024 * 1024
SOURCE_BLOBS = {
    "package.json": "0e8df818df4469815cec647c920a067b597d1ee2",
    "package-lock.json": "91b78b2a822095fd1baa75eadf8f537da69333a8",
    "pptx_render.py": "9f495f34ac3ac24bb8c26127e0156d4cc20dfcf0",
    "pptx_mapping.py": "704852426b33bad6e0e1b79130d621ac8779564a",
    "pptx_ooxml.py": "fc5d5b7a949ab4a98f1a213f3d0c73253f61bc43",
    "scripts/pptxgen_render.mjs": "2894f2b81a48a2a2d38aab30ff76bf6d709ecda7",
    "scripts/pptxgen_allowlist.json": "49d29e94dfd648f3510c5177656a7d43c21a39ce",
    "pptx_spec.py": "e7b4d6278132592884a7115a909990326268c644",
    "tests/test_pptx_mapping.py": "2f4f0bbc35eddff105cabb353c56daa4a73ac5ac",
    "tests/test_pptxgenjs_image.py": "f4985166a6bdd173f272c45c9300f80e7d0c5e4e",
    "LICENSE": "4ec4989b801bf1a3df6184d21f79e6e7ed5931f6",
}
EXPECTED_PATHS = frozenset("node_modules/" + path for path in (
    "core-util-is", "https", "image-size", "immediate", "inherits", "jszip",
    "jszip/node_modules/isarray", "jszip/node_modules/readable-stream",
    "jszip/node_modules/safe-buffer", "jszip/node_modules/string_decoder",
    "lie", "pako", "pptxgenjs", "pptxgenjs/node_modules/@types/node",
    "process-nextick-args", "setimmediate", "undici-types", "util-deprecate",
))
UNSUPPORTED = frozenset(("peerDependencies", "peerDependenciesMeta",
    "optionalDependencies", "hasInstallScript", "link", "inBundle", "dev", "optional"))


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def read_snapshot(source: Path) -> dict[str, bytes]:
    """Read once, rejecting symlinks and files from a different snapshot."""
    source = source.absolute()
    result = {}
    for name, expected in SOURCE_BLOBS.items():
        path = source / name
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError(f"symlink input is unsupported: {name}")
        if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"expected bounded regular source file: {name}")
        data = path.read_bytes()
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError(f"source grew beyond the size limit: {name}")
        if git_blob(data) != expected:
            raise ValueError(f"source Git blob mismatch: {name}")
        result[name] = data
    return result


def validate_original(manifest: dict, lock: dict) -> None:
    if manifest.get("dependencies") != {"exceljs": "4.4.0", "pptxgenjs": "4.0.1"}:
        raise ValueError("unsupported original direct dependencies")
    if manifest.get("overrides") != {"image-size": "2.0.4", "uuid": "14.0.3"}:
        raise ValueError("unsupported original overrides")
    if lock.get("lockfileVersion") != 3 or not isinstance(lock.get("packages"), dict):
        raise ValueError("only the reviewed npm lock v3 is supported")
    if lock["packages"].get("", {}).get("dependencies") != manifest["dependencies"]:
        raise ValueError("original manifest/lock mismatch")
    for path, entry in lock["packages"].items():
        if not path:
            continue
        if UNSUPPORTED.intersection(entry):
            raise ValueError(f"unsupported graph fields: {path}")
        if not entry.get("resolved", "").startswith("https://registry.npmjs.org/"):
            raise ValueError(f"non-registry package: {path}")
        if not entry.get("integrity") or not entry.get("version"):
            raise ValueError(f"unlocked package: {path}")


def validate_projection(original: dict, projected: dict) -> None:
    expected = json.loads(json.dumps(original))
    # npm records the private staging directory name and synchronizes engines
    # from the unchanged manifest; neither is a dependency/version change.
    expected["name"] = "Ironmate"
    expected["packages"] = {path: entry for path, entry in expected["packages"].items()
                            if not path or path in EXPECTED_PATHS}
    expected["packages"][""]["dependencies"] = {"pptxgenjs": "4.0.1"}
    expected["packages"][""]["engines"] = {"node": ">=24"}
    if set(projected.get("packages", {})) != {"", *EXPECTED_PATHS}:
        raise ValueError("npm projection has an unexpected package path set")
    if projected != expected:
        raise ValueError("npm changed a retained lock record or metadata")


def prepare(source: Path, output_dir: Path, npm: str = "npm") -> Path:
    """Return an atomic, private preparation directory; failures remove only ours."""
    source_root, output_dir = source.resolve(), output_dir.resolve()
    if output_dir == source_root or source_root in output_dir.parents:
        raise ValueError("output directory must be outside the source snapshot")
    snapshot = read_snapshot(source)
    manifest = json.loads(snapshot["package.json"])
    original = json.loads(snapshot["package-lock.json"])
    validate_original(manifest, original)
    executable = shutil.which(npm)
    if executable is None:
        raise ValueError("npm executable not found")
    executable = str(Path(executable).resolve())
    output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".nano-ironmate-pptx-", dir=output_dir))
    final = work.with_name(work.name[1:])
    try:
        project = work / "Ironmate"
        inputs = work / "inputs"
        project.mkdir()
        inputs.mkdir()
        for name, data in snapshot.items():
            dest = project / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if name in ("package.json", "package-lock.json"):
                (inputs / name).write_bytes(data)
        manifest["dependencies"] = {"pptxgenjs": "4.0.1"}
        candidate = (json.dumps(manifest, indent=2) + "\n").encode()
        (project / "package.json").write_bytes(candidate)
        for name in ("home", "config", "cache"):
            (work / name).mkdir()
        for name in ("user.npmrc", "global.npmrc"):
            (work / name).write_text("", encoding="utf-8")
        env = {k: v for k, v in os.environ.items()
               if not k.lower().startswith("npm_config_")
               and k not in ("NODE_OPTIONS", "NODE_PATH", "NODE_ENV")}
        env.update(HOME=str(work / "home"), XDG_CONFIG_HOME=str(work / "config"))
        command = [executable, "install", "--package-lock-only", "--offline",
                   "--ignore-scripts", "--no-audit", "--no-fund",
                   "--userconfig", str(work / "user.npmrc"),
                   "--globalconfig", str(work / "global.npmrc"),
                   "--cache", str(work / "cache")]
        version = subprocess.run([executable, "--version"], cwd=project, env=env,
                                 capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        run = subprocess.run(command, cwd=project, env=env, capture_output=True,
                             text=True, timeout=120)
        (work / "npm.stdout").write_text(run.stdout, encoding="utf-8")
        (work / "npm.stderr").write_text(run.stderr, encoding="utf-8")
        if run.returncode:
            raise ValueError(f"offline npm lock projection failed (exit {run.returncode})")
        if (project / "node_modules").exists():
            raise ValueError("unexpected node_modules during lock-only preparation")
        validate_projection(original, json.loads((project / "package-lock.json").read_bytes()))
        if (project / "package.json").read_bytes() != candidate:
            raise ValueError("npm changed the projected manifest")
        for name, data in snapshot.items():
            if name not in ("package.json", "package-lock.json") and (project / name).read_bytes() != data:
                raise ValueError(f"staged source changed: {name}")
        if read_snapshot(source) != snapshot:
            raise ValueError("source changed during preparation")
        receipt = {
            "feature": "ironmate-pptx", "repository": REPOSITORY, "source_commit": COMMIT,
            "checkout_head_verified": False, "source_git_blobs": SOURCE_BLOBS,
            "npm_version": version, "npm_executable": executable,
            "action": "offline official-npm lock-only preparation; no installation",
            "command_arguments": command[1:7] + [
                "--userconfig", "<private-work>/user.npmrc",
                "--globalconfig", "<private-work>/global.npmrc",
                "--cache", "<private-work>/cache"],
            "baseline_locked_package_records": len(original["packages"]) - 1,
            "candidate_locked_package_records": len(EXPECTED_PATHS),
            "retained_full_records_unchanged": True, "preserved_overrides": manifest["overrides"],
            "npm_root_metadata_changes": {"name": "Ironmate", "engines": {"node": ">=24"}},
            "projected_manifest_sha256": hashlib.sha256(candidate).hexdigest(),
            "projected_lock_sha256": hashlib.sha256((project / "package-lock.json").read_bytes()).hexdigest(),
            "installed_package_count": None, "installed_bytes": None, "installation_seconds": None,
        }
        (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        if final.exists():
            raise ValueError("result destination already exists")
        work.rename(final)
        return final
    except BaseException:
        shutil.rmtree(work)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--npm", default="npm")
    args = parser.parse_args()
    try:
        result = prepare(args.source, args.output_dir, args.npm)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"nano-node: {exc}\n")
    print(result)


if __name__ == "__main__":
    main()
