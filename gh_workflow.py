"""Single-file stdlib GitHub Actions wrapper; requires authenticated gh.

No YAML parser is bundled: preflight recognizes conservative common trigger
forms and reports unsupported YAML as unknown instead of guessing. GitHub is
the final authority for inputs and workflow validity.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

__version__ = "0.1.0"
__all__ = ["inspect_workflow", "execute_workflow", "WorkflowError"]


class WorkflowError(RuntimeError):
    def __init__(self, code, *, uncertain=False):
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain


def _text(value, name):
    if not isinstance(value, str) or not value or value.startswith("-") or any(ord(c) < 32 for c in value):
        raise ValueError(name + " must be a non-option string without control characters")
    return value


def _api(repo, path, *, method="GET", payload=None, timeout=30):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    argv = ["gh", "api", "--hostname", "github.com", "--method", method,
            "-H", "Accept: application/vnd.github+json",
            "-H", "X-GitHub-Api-Version: 2026-03-10", "repos/" + repo + "/" + path]
    env = os.environ.copy()
    env.update(GH_PROMPT_DISABLED="1", GH_PAGER="cat")
    # Prevent ambient repository substitution in gh endpoint placeholders.
    env.pop("GH_REPO", None)
    kwargs = dict(capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                  shell=False, check=False, env=env)
    if payload is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        argv.extend(["--input", "-"])
        kwargs["input"] = json.dumps(payload)
    try:
        proc = subprocess.run(argv, **kwargs)
    except FileNotFoundError as exc:
        raise WorkflowError("gh_not_found") from exc
    except subprocess.TimeoutExpired as exc:
        raise WorkflowError("timeout", uncertain=method != "GET") from exc
    except OSError as exc:
        raise WorkflowError("process_error", uncertain=method != "GET") from exc
    if proc.returncode:
        # Never copy raw stderr (tokens, workflow inputs) into persisted receipts.
        match = re.search(r"HTTP (\d{3})", proc.stderr or "")
        status = int(match[1]) if match else None
        code = {401: "authentication_required", 403: "permission_or_rate_limit",
                404: "not_found_or_inaccessible", 422: "dispatch_rejected"}.get(status, "gh_failed")
        raise WorkflowError(code, uncertain=method != "GET" and (status is None or status >= 500))
    if not proc.stdout.strip():
        return None
    try:
        return json.loads(proc.stdout)
    except (ValueError, TypeError) as exc:
        raise WorkflowError("invalid_json", uncertain=method != "GET") from exc


def _dispatch_trigger(yaml):
    """True/False/None for a deliberately small, fail-closed YAML subset.

    Quoted keys and CRLF are supported. Flow maps, aliases, multiline strings,
    directives, duplicate on keys and complex YAML require a real YAML parser.
    """
    lines = yaml.splitlines()
    # Reject block scalars globally so embedded 'on:' cannot look like a key.
    if any(re.search(r":\s*[>|&*{]", line.split("#", 1)[0]) for line in lines):
        return None
    meaningful = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if any("\t" in line or line.startswith(("---", "...", "%")) for line in meaningful):
        return None
    keys = [(i, re.fullmatch(r"(?:on|'on'|\"on\"):\s*(.*?)\s*(?:#.*)?", line))
            for i, line in enumerate(lines) if line and not line[0].isspace()]
    keys = [(i, m) for i, m in keys if m]
    if len(keys) != 1:
        return None
    index, match = keys[0]
    tail = match[1].split("#", 1)[0].strip()
    atom = r"(?:[a-z_]+|'[a-z_]+'|\"[a-z_]+\")"
    if tail:
        if re.fullmatch(atom, tail):
            return tail.strip("'\"") == "workflow_dispatch"
        if re.fullmatch(r"\[\s*" + atom + r"(?:\s*,\s*" + atom + r")*\s*\]", tail):
            return any(s.strip().strip("'\"") == "workflow_dispatch" for s in tail[1:-1].split(","))
        return None
    children = []
    for line in lines[index + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            break
        children.append(line)
    if not children:
        return False
    indent = len(children[0]) - len(children[0].lstrip())
    names = []
    for line in children:
        depth = len(line) - len(line.lstrip())
        if depth < indent:
            return None
        if depth == indent:
            key = re.fullmatch(r"\s*(" + atom + r"):\s*(?:#.*)?", line)
            if not key:
                return None
            names.append(key[1].strip("'\""))
    if len(names) != len(set(names)):
        return None
    return "workflow_dispatch" in names


def _yaml(repo, path, sha, timeout):
    obj = _api(repo, "contents/" + quote(path, safe="/") + "?ref=" + quote(sha, safe=""), timeout=timeout)
    if not isinstance(obj, dict) or obj.get("encoding") != "base64":
        raise WorkflowError("unsupported_content")
    try:
        return base64.b64decode(obj["content"]).decode("utf-8")
    except (KeyError, ValueError, UnicodeError) as exc:
        raise WorkflowError("invalid_content") from exc


def _resolve_ref(repo, ref, timeout):
    obj = _api(repo, "commits/" + quote(ref, safe=""), timeout=timeout)
    sha = obj.get("sha") if isinstance(obj, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise WorkflowError("invalid_commit")
    return sha


def inspect_workflow(repo, workflow, ref=None, *, timeout=30):
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or any(p in (".", "..") for p in repo.split("/")):
        raise ValueError("repo must be owner/name on github.com")
    workflow = _text(workflow, "workflow")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", workflow) or workflow in (".", ".."):
        raise ValueError("workflow must be a numeric ID or filename")
    info = _api(repo, "", timeout=timeout)
    if not isinstance(info, dict) or not isinstance(info.get("default_branch"), str):
        raise WorkflowError("invalid_repository_response")
    default = _text(info["default_branch"], "default_branch")
    ref = default if ref is None else _text(ref, "ref")
    wf = _api(repo, "actions/workflows/" + quote(workflow, safe=""), timeout=timeout)
    if (not isinstance(wf, dict) or not isinstance(wf.get("path"), str)
            or not isinstance(wf.get("state"), str) or isinstance(wf.get("id"), bool)
            or not isinstance(wf.get("id"), int) or wf["id"] <= 0):
        raise WorkflowError("invalid_workflow_response")
    path = wf["path"]
    if not path.startswith(".github/workflows/") or ".." in path.split("/") or not path.endswith((".yml", ".yaml")):
        raise WorkflowError("unsupported_workflow_path")
    default_sha = _resolve_ref(repo, default, timeout)
    target_sha = default_sha if ref == default else _resolve_ref(repo, ref, timeout)
    default_trigger = _dispatch_trigger(_yaml(repo, path, default_sha, timeout))
    target_trigger = default_trigger if target_sha == default_sha else _dispatch_trigger(_yaml(repo, path, target_sha, timeout))
    reasons = []
    for label, trigger in (("default", default_trigger), ("target", target_trigger)):
        if trigger is not True:
            reasons.append(label + ("_dispatch_missing" if trigger is False else "_yaml_unknown"))
    if wf["state"] not in ("active", "disabled_inactivity", "disabled_manually"):
        reasons.append("workflow_state_unsupported")
    return {"schema_version": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
            "repository": repo, "workflow_id": wf["id"], "workflow_path": path,
            "workflow_state": wf["state"], "default_branch": default,
            "default_sha": default_sha, "ref": ref, "target_sha": target_sha,
            "dispatch_default": default_trigger, "dispatch_target": target_trigger,
            "ready": not reasons, "reasons": reasons, "status": "inspected",
            "enabled": False, "run_id": None, "run_url": None, "conclusion": None}


def execute_workflow(repo, workflow, ref=None, *, apply=False, inputs=None, timeout=30):
    if inputs is not None and (not isinstance(inputs, dict) or any(not isinstance(k, str) for k in inputs)):
        raise ValueError("inputs must be a JSON object")
    if inputs is not None:
        json.dumps(inputs, allow_nan=False)
    receipt = inspect_workflow(repo, workflow, ref, timeout=timeout)
    if not receipt["ready"]:
        receipt["status"] = "blocked"
        return receipt
    if not apply:
        receipt["status"] = "planned"
        return receipt
    endpoint = "actions/workflows/" + str(receipt["workflow_id"])
    stage = "enable"
    try:
        if receipt["workflow_state"] != "active":
            _api(repo, endpoint + "/enable", method="PUT", timeout=timeout)
            receipt["enabled"] = True
        stage = "dispatch"
        # Branch/tag ref is deliberately retained: GitHub dispatch accepts these.
        # target_sha is an observation; exact executed SHA is verified below.
        data = _api(repo, endpoint + "/dispatches", method="POST",
                    payload={"ref": receipt["ref"], "inputs": inputs or {}}, timeout=timeout)
        receipt["status"] = "dispatched"
        if data is None:  # Older endpoint behavior: do not infer a run from newest.
            receipt["status"] = "dispatched_run_unresolved"
            receipt["target_sha_role"] = "pre_dispatch_observation"
            try:
                receipt["ref_sha_after"] = _resolve_ref(repo, receipt["ref"], timeout)
            except WorkflowError as exc:
                receipt["ref_sha_after_error"] = exc.code
            return receipt
        run_id = data.get("workflow_run_id") if isinstance(data, dict) else None
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            raise WorkflowError("invalid_dispatch_response", uncertain=True)
        receipt["run_id"] = run_id
        receipt["run_url"] = "https://github.com/" + repo + "/actions/runs/" + str(run_id)
        stage = "observe_run"
        run = _api(repo, "actions/runs/" + str(run_id), timeout=timeout)
        if (not isinstance(run, dict) or not isinstance(run.get("head_sha"), str)
                or not isinstance(run.get("status"), str) or "conclusion" not in run):
            raise WorkflowError("invalid_run_response")
        receipt.update(executed_sha=run["head_sha"], run_status=run["status"], conclusion=run["conclusion"])
        receipt["sha_matches"] = run["head_sha"] == receipt["target_sha"]
        if not receipt["sha_matches"]:
            receipt["status"] = "dispatched_ref_changed"
    except WorkflowError as exc:
        receipt.update(status="observation_failed" if stage == "observe_run" else
                       "mutation_uncertain" if exc.uncertain else "mutation_failed",
                       failed_stage=stage, error=exc.code)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo")
    parser.add_argument("workflow")
    parser.add_argument("--ref")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--inputs", help="JSON object, forwarded without recording values")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--receipt", type=Path, help="append a JSONL receipt")
    args = parser.parse_args(argv)
    try:
        inputs = json.loads(args.inputs) if args.inputs is not None else None
        if inputs is not None and not isinstance(inputs, dict):
            raise ValueError("inputs must be a JSON object")
        if inputs is not None:
            json.dumps(inputs, allow_nan=False)
        receipt = execute_workflow(args.repo, args.workflow, args.ref, apply=args.apply,
                                   inputs=inputs, timeout=args.timeout)
    except (WorkflowError, ValueError, RecursionError) as exc:
        receipt = {"status": "preflight_failed", "error": exc.code if isinstance(exc, WorkflowError) else "invalid_argument"}
    output = json.dumps(receipt, ensure_ascii=False)
    print(output)
    if args.receipt:
        try:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            with args.receipt.open("a", encoding="utf-8") as stream:
                stream.write(output + "\n")
        except OSError:
            print("receipt_write_failed", file=sys.stderr)
            return 1
    return 0 if receipt["status"] in ("planned", "dispatched") else 1


if __name__ == "__main__":
    raise SystemExit(main())
