#!/usr/bin/env python3
"""Prepare one explicitly reviewed Node feature with npm, offline; never install.

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
BTK_FEATURE = "browser-test-kit-document-mirrors"
BTK_REPOSITORY = "myon-bioinformatics/browser-test-kit"
BTK_COMMIT = "111f6d78befae28acfb198a45b72e5aa0e59dcde"
BTK_SOURCE_BLOBS = {
    "package.json": "9dbada6ad25af5051818d4399796d8d2cfc400c6",
    "package-lock.json": "b0191e266c3516596cb4c6a9ad69b47628551145",
    "tools/mirror_facts.mjs": "9b63b223619e2051aa1df81a574b8734ae4fbd17",
    "tools/mirror_check.py": "c37ee194ca98245755458ce1a17f41a1aea47c79",
    "tests/browserless/mirror.spec.ts": "30d14101cd1b294a05d1b817cd908e824858eea8",
    "tests/python/test_mirror_check.py": "f7ae34a8e1e738d44f9a7af5e7dc4e5332b2b9d5",
    "playwright.browserless.config.ts": "1d3990ea0e09c37a8ab82d74b8368a9d97da436f",
    "fixtures/mirror.md": "7f4ee2e50ebce5682d508d382caa8e5a76a4b750",
    "LICENSE": "4ec4989b801bf1a3df6184d21f79e6e7ed5931f6",
}
BTK_SELECTED = {"@playwright/test": ">=1.64.1", "linkedom": "^0.18.13",
                "markdown-it": "^15.0.3", "marked": "^18.1.1"}
BTK_EXPECTED_PATHS = frozenset("node_modules/" + path for path in (
    "@playwright/test", "argparse", "boolbase", "css-select", "css-what", "cssom",
    "dom-serializer", "domelementtype", "domhandler", "domutils", "entities",
    "html-escaper", "htmlparser2", "htmlparser2/node_modules/dom-serializer",
    "htmlparser2/node_modules/dom-serializer/node_modules/entities",
    "htmlparser2/node_modules/domelementtype", "htmlparser2/node_modules/domhandler",
    "htmlparser2/node_modules/domutils", "htmlparser2/node_modules/entities",
    "linkedom", "linkify-it", "markdown-it", "marked", "mdurl", "nth-check",
    "playwright", "playwright-core", "punycode.js", "uc.micro", "uhyphen",
))
MARKDOWN_FEATURE = "markdown-toml"
MARKDOWN_REPOSITORY = "myon-bioinformatics/markdown"
MARKDOWN_COMMIT = "6ffef17031cf10edbeda71718c44ad739735f87e"
# --source points to tests/mirror, not the repository root. This selector is
# dependency-only: it never stages or chooses application/test source files.
MARKDOWN_SOURCE_BLOBS = {
    "package.json": "96b2213e31ecb3033543142d22705b8b833d07ad",
    "package-lock.json": "2d97cdb6c5d5e809ab94500c9903c88cf54d1c4d",
}
MARKDOWN_SELECTED = {"smol-toml": "1.9.1"}
MARKDOWN_EXPECTED_PATHS = frozenset({"node_modules/smol-toml"})
UNSUPPORTED = frozenset(("peerDependencies", "peerDependenciesMeta",
    "optionalDependencies", "hasInstallScript", "link", "inBundle", "dev", "optional"))


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def read_snapshot(source: Path, *, blobs=None) -> dict[str, bytes]:
    """Read once, rejecting symlinks and files from a different snapshot."""
    source = source.absolute()
    result = {}
    for name, expected in (SOURCE_BLOBS if blobs is None else blobs).items():
        path = source / name
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError(f"symlink input is unsupported: {name}")
        if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"expected bounded regular source file: {name}")
        data = path.read_bytes()
        if len(data) > MAX_INPUT_BYTES:
            raise ValueError(f"source grew beyond the size limit: {name}")
        actual = git_blob(data)
        if actual != expected:
            raise ValueError(f"source Git blob mismatch: {name}; expected {expected}, got {actual}. "
                             "Review the changed inputs and coordinate a fixed parent selector-pin update; "
                             "do not disable this guard.")
        result[name] = data
    return result


def validate_original(manifest: dict, lock: dict, *, feature="ironmate-pptx") -> None:
    if feature == "ironmate-pptx":
        key, expected = "dependencies", {"exceljs": "4.4.0", "pptxgenjs": "4.0.1"}
        overrides = {"image-size": "2.0.4", "uuid": "14.0.3"}
    elif feature == BTK_FEATURE:
        key, expected = "devDependencies", {**BTK_SELECTED, "exceljs": "^4.4.0"}
        overrides = {"uuid": "^14.0.3", "minimatch": "^11.0.1"}
    elif feature == MARKDOWN_FEATURE:
        key, expected = "devDependencies", {
            "linkedom": "0.18.13", "markdown-it": "15.0.3", "marked": "18.1.1",
            "string-width": "8.3.0", **MARKDOWN_SELECTED,
        }
        overrides = None
    else:
        raise ValueError("unsupported named feature")
    if manifest.get(key) != expected:
        raise ValueError("unsupported original direct dependencies")
    if manifest.get("overrides") != overrides:
        raise ValueError("unsupported original overrides")
    if lock.get("lockfileVersion") != 3 or not isinstance(lock.get("packages"), dict):
        raise ValueError("only the reviewed npm lock v3 is supported")
    if lock["packages"].get("", {}).get(key) != manifest[key]:
        raise ValueError("original manifest/lock mismatch")
    for path, entry in lock["packages"].items():
        if not path:
            continue
        allowed = set()
        if feature in (BTK_FEATURE, MARKDOWN_FEATURE):
            if entry.get("dev") is not True:
                raise ValueError(f"unexpected development marker: {path}")
            allowed.add("dev")
            # One reviewed optional peer, NOT an allow-all peer resolver.
            # Exact input blobs and full retained record equality remain required.
            if path == "node_modules/linkedom":
                if (entry.get("peerDependencies") != {"canvas": ">= 2"}
                        or entry.get("peerDependenciesMeta") != {"canvas": {"optional": True}}):
                    raise ValueError("unsupported linkedom optional canvas peer")
                allowed.update(("peerDependencies", "peerDependenciesMeta"))
            if path == "node_modules/canvas" or path.endswith("/node_modules/canvas"):
                raise ValueError("canvas is absent from the reviewed feature closure")
        if (UNSUPPORTED - allowed).intersection(entry):
            raise ValueError(f"unsupported graph fields: {path}")
        if not entry.get("resolved", "").startswith("https://registry.npmjs.org/"):
            raise ValueError(f"non-registry package: {path}")
        if not entry.get("integrity") or not entry.get("version"):
            raise ValueError(f"unlocked package: {path}")


def validate_projection(original: dict, projected: dict, *, feature="ironmate-pptx") -> None:
    if feature == "ironmate-pptx":
        paths, name, key, selected = EXPECTED_PATHS, "Ironmate", "dependencies", {"pptxgenjs": "4.0.1"}
    elif feature == BTK_FEATURE:
        paths, name, key, selected = BTK_EXPECTED_PATHS, "browser-test-kit", "devDependencies", BTK_SELECTED
    elif feature == MARKDOWN_FEATURE:
        paths, name, key, selected = MARKDOWN_EXPECTED_PATHS, "markdown-toml", "devDependencies", MARKDOWN_SELECTED
    else:
        raise ValueError("unsupported named feature")
    expected = json.loads(json.dumps(original))
    # npm records the private staging directory name and synchronizes engines
    # from the unchanged manifest; neither is a dependency/version change.
    expected["name"] = name
    expected["packages"] = {path: entry for path, entry in expected["packages"].items()
                            if not path or path in paths}
    expected["packages"][""][key] = selected
    if feature != MARKDOWN_FEATURE:
        expected["packages"][""]["engines"] = {"node": ">=24"}
    if set(projected.get("packages", {})) != {"", *paths}:
        raise ValueError("npm projection has an unexpected package path set")
    if projected != expected:
        raise ValueError("npm changed a retained lock record or metadata")


def prepare(source: Path, output_dir: Path, npm: str = "npm", *,
            feature="ironmate-pptx", dependencies_only=False) -> Path:
    """Return an atomic, private preparation directory; failures remove only ours."""
    source_root, output_dir = source.resolve(), output_dir.resolve()
    if output_dir == source_root or source_root in output_dir.parents:
        raise ValueError("output directory must be outside the source snapshot")
    if feature == "ironmate-pptx":
        blobs, paths = SOURCE_BLOBS, EXPECTED_PATHS
        repository, commit, project_name = REPOSITORY, COMMIT, "Ironmate"
        key, selected = "dependencies", {"pptxgenjs": "4.0.1"}
    elif feature == BTK_FEATURE:
        blobs, paths = BTK_SOURCE_BLOBS, BTK_EXPECTED_PATHS
        repository, commit, project_name = BTK_REPOSITORY, BTK_COMMIT, "browser-test-kit"
        key, selected = "devDependencies", BTK_SELECTED
    elif feature == MARKDOWN_FEATURE:
        if not dependencies_only:
            raise ValueError("markdown-toml requires --dependencies-only")
        blobs, paths = MARKDOWN_SOURCE_BLOBS, MARKDOWN_EXPECTED_PATHS
        repository, commit, project_name = MARKDOWN_REPOSITORY, MARKDOWN_COMMIT, "markdown-toml"
        key, selected = "devDependencies", MARKDOWN_SELECTED
    else:
        raise ValueError("unsupported named feature")
    if dependencies_only:
        blobs = {name: blobs[name] for name in ("package.json", "package-lock.json")}
    snapshot = read_snapshot(source, blobs=blobs)
    manifest = json.loads(snapshot["package.json"])
    original = json.loads(snapshot["package-lock.json"])
    validate_original(manifest, original, feature=feature)
    executable = shutil.which(npm)
    if executable is None:
        raise ValueError("npm executable not found")
    executable = str(Path(executable).resolve())
    output_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f".nano-{feature}-", dir=output_dir))
    final = work.with_name(work.name[1:])
    try:
        project = work / project_name
        inputs = work / "inputs"
        project.mkdir()
        inputs.mkdir()
        for name, data in snapshot.items():
            dest = project / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            if name in ("package.json", "package-lock.json"):
                (inputs / name).write_bytes(data)
        manifest[key] = selected
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
        validate_projection(original, json.loads((project / "package-lock.json").read_bytes()), feature=feature)
        if (project / "package.json").read_bytes() != candidate:
            raise ValueError("npm changed the projected manifest")
        for name, data in snapshot.items():
            if name not in ("package.json", "package-lock.json") and (project / name).read_bytes() != data:
                raise ValueError(f"staged source changed: {name}")
        if read_snapshot(source, blobs=blobs) != snapshot:
            raise ValueError("source changed during preparation")
        receipt = {
            "feature": feature, "repository": repository, "source_commit": commit,
            "checkout_head_verified": False, "source_git_blobs": blobs,
            "npm_version": version, "npm_executable": executable,
            "action": "offline official-npm lock-only preparation; no installation",
            "command_arguments": command[1:7] + [
                "--userconfig", "<private-work>/user.npmrc",
                "--globalconfig", "<private-work>/global.npmrc",
                "--cache", "<private-work>/cache"],
            "baseline_locked_package_records": len(original["packages"]) - 1,
            "candidate_locked_package_records": len(paths),
            "retained_full_records_unchanged": True, "preserved_overrides": manifest.get("overrides"),
            "npm_root_metadata_changes": ({"name": project_name} if feature == MARKDOWN_FEATURE
                                          else {"name": project_name, "engines": {"node": ">=24"}}),
            "projected_manifest_sha256": hashlib.sha256(candidate).hexdigest(),
            "projected_lock_sha256": hashlib.sha256((project / "package-lock.json").read_bytes()).hexdigest(),
            "installed_package_count": None, "installed_bytes": None, "installation_seconds": None,
        }
        if dependencies_only:
            receipt.update(dependencies_only=True, application_source_staged=False,
                           verification_scope="two pinned package inputs only; live tests are not selected or copied")
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
    parser.add_argument("--feature", choices=("ironmate-pptx", BTK_FEATURE, MARKDOWN_FEATURE), default="ironmate-pptx")
    parser.add_argument("--dependencies-only", action="store_true",
                        help="verify/stage only package.json and package-lock.json; never copy historical source/tests")
    args = parser.parse_args()
    try:
        result = prepare(args.source, args.output_dir, args.npm, feature=args.feature,
                         dependencies_only=args.dependencies_only)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"nano-node: {exc}\n")
    print(result)


if __name__ == "__main__":
    main()
