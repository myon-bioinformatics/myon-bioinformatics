#!/usr/bin/env python3
"""Narrow MCP chat-client comparison preparation; stdlib until gated workers.

Safe modes are verify and plan. There is deliberately NO install/download mode.
serve/client must not be run before separate authorization and an exclusive
measurement window. --allow-loopback-execution records that operator decision;
it is not an authorization source or an OS sandbox.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import resource
import socket
import sys
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
REVISION = "06cdc8e4e14d50de45c8b8122a5ccff6911c3404"
CLIENT_FILES = (
    "__init__.py", "chat_sim.py", "record.py", "mock/__init__.py",
    "mock/common.py", "catalog.py", "wikipedia_tool.py", "markdown_lib.py",
    "pixiv_dictionary_tool.py", "pixiv_markup_normalize.py",
)
CASES = (
    ("municipality", "find_municipalities", {"query": "Exampleville"}, False),
    ("unicode_empty", "find_municipalities", {"query": "サンプル市"}, False),
    ("station", "find_stations", {"municipality_code": "90001"}, False),
    ("empty", "find_stations", {"municipality_code": "00000"}, False),
    ("transaction", "find_transaction_prices", {"municipality_code": "90001", "year": 2025}, False),
    ("missing_argument", "find_municipalities", {}, True),
    ("invalid_type", "find_transaction_prices", {"municipality_code": 90001, "year": 2025}, True),
    ("unknown_tool", "find_transaction_price", {}, True),
)
EXPECTED_SUCCESS = {
    "municipality": [{"code": "90001", "name": "Exampleville", "prefecture": "Example Region"}],
    "unicode_empty": [],
    "station": [{"name": "Exampleville", "line": "JR"}],
    "empty": [],
    "transaction": [{"municipality_code": "90001", "year": 2025, "price_yen": 52_000_000,
                     "property_type": "condominium", "source": "mock"}],
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def verify(root: Path = ROOT) -> dict:
    receipt = json.loads((root / "source-receipt.json").read_text())
    if receipt["revision"] != REVISION:
        raise ValueError("unexpected revision")
    for row in receipt["files"]:
        path = root / row["local_path"]
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"missing/non-regular source: {row['local_path']}")
        data = path.read_bytes()
        if len(data) != row["bytes"] or digest(data) != row["sha256"]:
            raise ValueError(f"source changed: {row['local_path']}")
        if row.get("git_blob") and git_blob(data) != row["git_blob"]:
            raise ValueError(f"Git blob mismatch: {row['local_path']}")
    return {"status": "verified", "revision": REVISION, "files": len(receipt["files"]),
            "receipt_sha256": digest((root / "source-receipt.json").read_bytes())}


def endpoint(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or
            parsed.username is not None or parsed.password is not None or
            parsed.path != "/mcp" or parsed.query or parsed.fragment or
            parsed.port is None or not (1 <= parsed.port <= 65535)):
        raise ValueError("only explicit http://127.0.0.1:PORT/mcp is permitted")
    return "127.0.0.1", parsed.port


def audit_policy(event: str, args, target: tuple[str, int] | None) -> bool:
    """Pure decision helper so the gate is tested without opening a socket."""
    if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork"}:
        return False
    if event == "socket.connect":
        address = args[1]
        return target is not None and isinstance(address, tuple) and address[:2] == target
    if event == "socket.getaddrinfo":
        return target is not None and args[0] in {"127.0.0.1", b"127.0.0.1"} and int(args[1]) == target[1]
    if event == "socket.bind":
        address = args[1]
        return target is None and isinstance(address, tuple) and address[0] == "127.0.0.1"
    return True


def restrict_worker(target: tuple[str, int] | None) -> list[dict]:
    denied = []

    def audit(event, args):
        if not audit_policy(event, args, target):
            denied.append({"event": event, "detail": repr(args)[:500]})
            raise PermissionError(f"comparison worker blocked {event}")

    sys.addaudithook(audit)
    return denied


def clean_environment() -> None:
    """No inherited proxies, provider keys, live flags or alternate source paths."""
    kept = {name: os.environ[name] for name in ("PATH", "LANG", "LC_ALL", "TZ", "HOME", "TMPDIR") if name in os.environ}
    os.environ.clear()
    os.environ.update(kept)
    os.environ.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0",
                       "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1",
                       "FASTMCP_SHOW_SERVER_BANNER": "false", "MCP_TOOL_DELAY_SECONDS": "0"})


def activate_source() -> None:
    # Exact intact source slice; no replacement __init__, import stubs or fake SDK.
    sys.path.insert(0, str(ROOT / "runtime-source" / "src"))


def packages() -> list[dict]:
    return sorted(({"name": d.metadata["Name"], "version": d.version}
                   for d in importlib.metadata.distributions()), key=lambda x: x["name"].lower())


async def client_workload(url: str, run_tag: str) -> dict:
    from mcp_toolcall_lab.chat_sim import send_direct, send_via_chat

    rows = []
    for name, tool, arguments, expect_error in CASES:
        for mode in ("direct", "chat"):
            kwargs = {"tool_name": tool, "arguments": arguments,
                      "trace_id": f"nano-{run_tag}-{name}-{mode}"}
            async with asyncio.timeout(10):
                if mode == "chat":
                    trace = await send_via_chat(url, user_text=f"Synthetic case: {name}",
                                               chat_id=f"chat_fixture_{name}",
                                               call_id=f"call_fixture_{name}", **kwargs)
                    result = trace.mcp_result
                    wire = {"chat_id": trace.chat_id, "call_id": trace.call_id,
                            "user_message": trace.user_message,
                            "assistant_tool_call_message": trace.assistant_tool_call_message,
                            "tool_result_message": trace.tool_result_message}
                    if wire["tool_result_message"]["tool_call_id"] != wire["call_id"]:
                        raise AssertionError("chat correlation mismatch")
                else:
                    result = await send_direct(url, **kwargs)
                    wire = None
            if bool(result.is_error) != expect_error:
                raise AssertionError(f"unexpected error status: {name}/{mode}")
            if not expect_error:
                # Independent fixed oracle, not a second dispatch to the same body.
                # Shape is the unchanged test_httpx_protocol success/empty contract.
                structured = result.structured_content
                if not isinstance(structured, dict) or structured.get("result") != EXPECTED_SUCCESS[name]:
                    raise AssertionError(f"independent expected result mismatch: {name}/{mode}")
                if not EXPECTED_SUCCESS[name] and result.content != []:
                    raise AssertionError(f"empty successful content mismatch: {name}/{mode}")
            # Complete real Pydantic SDK result: no removed metadata/content fields.
            rows.append({"case": name, "mode": mode, "chat_wire": wire,
                         "mcp_result": result.model_dump(mode="json", by_alias=True, exclude_none=False)})
    return {"cases": rows}


def run_client(args) -> None:
    target = endpoint(args.url)
    clean_environment()
    denied = restrict_worker(target)
    activate_source()
    started = time.perf_counter()
    result = asyncio.run(client_workload(args.url, args.run_tag))
    elapsed = time.perf_counter() - started
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if denied:
        raise AssertionError("worker attempted a forbidden operation")
    imported = sorted(name for name in sys.modules if name == "fastmcp" or name.startswith("fastmcp."))
    if imported:
        raise AssertionError("public chat client unexpectedly imported FastMCP")
    write_json(Path(args.output), {"status": "completed", "output": result,
               "output_sha256": digest(canonical(result)), "seconds_import_and_calls": elapsed,
               "cli_completion_ru_maxrss_raw": rss,
               "ru_maxrss_unit": "KiB on Linux; bytes on macOS; platform must be recorded",
               "installed_packages": packages(), "python": sys.version,
               "run_tag": args.run_tag, "calls": 2 * len(CASES), "forbidden_events": denied,
               "fastmcp_imports": imported})


def run_server(args) -> None:
    clean_environment()
    os.environ["MCP_TOOLCALL_LOG"] = str(Path(args.log).resolve())
    denied = restrict_worker(None)  # Listener accepts clients; no outgoing connects.
    activate_source()
    import uvicorn
    from mcp_toolcall_lab.server import create_mcp

    # Actual original server and tool implementations, not a new protocol mock.
    app = create_mcp().http_app(path="/mcp")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))

        async def serve():
            task = asyncio.create_task(server.serve(sockets=[sock]))
            try:
                async with asyncio.timeout(10):
                    while not server.started:
                        if task.done():
                            await task
                            raise RuntimeError("server exited before readiness")
                        await asyncio.sleep(0.01)
                write_json(Path(args.ready), {"url": f"http://127.0.0.1:{sock.getsockname()[1]}/mcp",
                           "pid": os.getpid(), "fixture": "unchanged server.create_mcp",
                           "installed_packages": packages()})
                # An independent upper bound even if the orchestrator disappears.
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=180)
                except TimeoutError:
                    pass
            finally:
                server.should_exit = True
                try:
                    await asyncio.wait_for(task, timeout=5)
                except TimeoutError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            if denied:
                raise AssertionError("server attempted a forbidden operation")

        asyncio.run(serve())


def plan() -> dict:
    return {"status": "preparation_only_not_measured", "revision": REVISION,
            "install_gate": "separate explicit approval and exclusive window; installer is separate measure_mcp.py",
            "requirements_rows_not_installed_counts": {"server": 72, "client": 30},
            "planned_install_order": [[1, "server"], [1, "client"], [2, "client"], [2, "server"], [3, "server"], [3, "client"]],
            "per_install_command_template": ["<uv-0.13.4>", "--no-config", "pip", "install", "--python", "<fresh-venv>/bin/python", "--require-hashes", "--only-binary", ":all:", "--no-deps", "--link-mode", "copy", "--index-url", "https://pypi.org/simple", "--cache-dir", "<fresh-cache>", "-r", "requirements-<server-or-client>.txt"],
            "network_retries": 0, "install_timeout_seconds": 300,
            "future_runtime_comparison": "same real client functions, same separately counted unchanged FastMCP server fixture",
            "future_workload_repetitions": 3, "future_worker_counts": [1, 2],
            "calls_per_worker": 2 * len(CASES), "fresh_servers_per_batch": 1,
            "selected_environment_trial": None, "installed_packages": None,
            "install_seconds": None, "venv_bytes": None, "output_parity": None,
            "runtime_seconds": None, "rss_bytes": None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("verify")
    sub.add_parser("plan")
    server = sub.add_parser("serve")
    server.add_argument("--ready", required=True)
    server.add_argument("--log", required=True)
    server.add_argument("--allow-loopback-execution", action="store_true")
    client = sub.add_parser("client")
    client.add_argument("--url", required=True)
    client.add_argument("--output", required=True)
    client.add_argument("--run-tag", required=True)
    client.add_argument("--allow-loopback-execution", action="store_true")
    args = parser.parse_args(argv)
    if args.mode in {"serve", "client"} and not args.allow_loopback_execution:
        parser.error("loopback execution gate is closed; separate approval/window required")
    verify()
    if args.mode == "verify":
        print(json.dumps(verify(), indent=2))
    elif args.mode == "plan":
        print(json.dumps(plan(), indent=2))
    elif args.mode == "serve":
        run_server(args)
    else:
        run_client(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
