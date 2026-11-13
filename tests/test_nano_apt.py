"""nano_apt: offline apt planning, the Ironmate adoption experiment, and fatal dpkg observations (stdlib only, runs under python -S)."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


# ═══ · nano apt ══════════════════════════════════════════════════════════════════════════════════════════
# Offline tests for tools/nano_apt.py: fake apt/dpkg; one real read-only dpkg-query check.

SPEC = importlib.util.spec_from_file_location("nano_apt", Path(__file__).parents[1] / "tools/nano_apt.py")
nano = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(nano)

FAKE_BIN = {"apt-get": "/fake/apt-get", "dpkg-query": "/fake/dpkg-query", "sudo": "/fake/sudo"}
ACQ = nano.ACQUIRE


def apt(*rest):
    return ["/fake/apt-get"] + ACQ + list(rest)


class Fake:
    """Stands in for subprocess.run; `installed` maps package -> version."""

    def __init__(self, installed, apt_rc=0, apt_output=None):
        self.installed, self.apt_rc, self.calls = dict(installed), apt_rc, []
        self.apt_output = apt_output or {}

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if argv[0].endswith("dpkg-query"):
            lines = ["%s ii  %s" % (n, v) for n, v in self.installed.items() if n in argv]
            return subprocess.CompletedProcess(argv, 0 if len(lines) == len(argv[3:]) else 1,
                                               stdout="\n".join(lines) + "\n", stderr="")
        if kwargs.get("stderr") == subprocess.STDOUT:  # --log mode: apt output goes to the log file
            kwargs["stdout"].write(self.apt_output.get("update" if "update" in argv else "install", ""))
        if "install" in argv and "--download-only" not in argv:
            if self.apt_rc == 0:
                for item in argv[argv.index("install") + 3:]:
                    name, _, version = item.partition("=")
                    self.installed[name] = version or "9.9"
        return subprocess.CompletedProcess(argv, self.apt_rc)


class NanoAptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.err = io.StringIO()

    def lst(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def call(self, args, fake, euid=0, which=None):
        which = FAKE_BIN if which is None else which
        out = io.StringIO()
        with patch.object(nano.subprocess, "run", fake), \
                patch.object(nano.shutil, "which", lambda name, path=None: which.get(name)), \
                patch.object(nano.os, "geteuid", lambda: euid), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(self.err):
            code = nano.main(args)
        return code, (json.loads(out.getvalue()) if out.getvalue() else None)

    def test_parse_comments_pins_and_merge(self):
        a = self.lst("a.txt", "# runtime\n\ntzdata\nlibblas3=3.12.1-6\n")
        b = self.lst("b.txt", "tzdata=2025b-4\ngit\n")
        self.assertEqual(nano.merge([a, b]),
                         {"tzdata": "2025b-4", "libblas3": "3.12.1-6", "git": None})

    def test_bad_lines_and_conflicts_fail_closed(self):
        for text in ("Git\n", "git extra\n", "git # inline\n", "-y\n", "git=\n", "a=1\na=2\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                nano.read_list(self.lst("bad.txt", text))
        with self.assertRaises(ValueError):
            nano.merge([self.lst("x.txt", "a=1\n"), self.lst("y.txt", "a=2\n")])

    def test_non_regular_and_oversized_inputs_rejected(self):
        link = self.root / "link.txt"
        link.symlink_to(self.lst("real.txt", "git\n"))
        with self.assertRaises(ValueError):
            nano.read_list(link)
        with self.assertRaises(ValueError):
            nano.read_list(self.lst("big.txt", "git\n" * (nano.MAX_BYTES // 4 + 1)))
        code, _ = self.call(["--list", str(self.root / "missing.txt")], Fake({}))
        self.assertEqual(code, 2)

    def test_report_mode_runs_only_dpkg_query(self):
        fake = Fake({"git": "1:2.0"})
        code, result = self.call(["--list", self.lst("a.txt", "git\ntzdata\n")], fake)
        self.assertEqual((code, result["status"], result["to_install"]), (1, "missing", ["tzdata"]))
        self.assertEqual(result["packages"]["git"]["state"], "ok")
        self.assertEqual([argv[0] for argv, _ in fake.calls], ["/fake/dpkg-query"])
        self.assertEqual(result["executed"], [])

    def test_satisfied_install_does_nothing(self):
        fake = Fake({"git": "1:2.0"})
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install"], fake)
        self.assertEqual((code, result["status"], result["executed"]), (0, "satisfied", []))
        self.assertEqual(len(fake.calls), 1)

    def test_install_updates_then_installs_only_missing_and_verifies(self):
        fake = Fake({"git": "1:2.0", "libblas3": "3.11"})
        lists = self.lst("a.txt", "git\ntzdata\nlibblas3=3.12.1-6\n")
        code, result = self.call(["--list", lists, "--install"], fake)
        self.assertEqual((code, result["status"]), (0, "installed"))
        self.assertEqual(result["executed"], [
            apt("update"),
            apt("install", "-y", "--no-install-recommends", "libblas3=3.12.1-6", "tzdata")])
        apt_calls = [(argv, kw) for argv, kw in fake.calls if argv[0] == "/fake/apt-get"]
        self.assertEqual(len(apt_calls), 2)
        for _, kwargs in apt_calls:
            self.assertEqual(kwargs["env"]["DEBIAN_FRONTEND"], "noninteractive")
            self.assertEqual(set(kwargs["env"]), {"PATH", "DEBIAN_FRONTEND", "LANG"})

    def test_failed_apt_stops_and_reports(self):
        fake = Fake({}, apt_rc=100)
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install"], fake)
        self.assertEqual((code, result["status"]), (3, "failed"))
        self.assertEqual(result["executed"], [apt("update")])

    def test_install_that_does_not_land_is_failed(self):
        class Silent(Fake):
            def __call__(self, argv, **kwargs):
                if "install" in argv:
                    self.calls.append((list(argv), kwargs))
                    return subprocess.CompletedProcess(argv, 0)
                return super().__call__(argv, **kwargs)
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install"], Silent({}))
        self.assertEqual((code, result["status"]), (3, "failed"))

    def test_non_root_uses_sudo_or_stops(self):
        fake = Fake({})
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install"], fake, euid=1000)
        self.assertEqual(code, 0)
        self.assertEqual(result["executed"][0], ["/fake/sudo", "-n"] + apt("update"))
        bare = {k: v for k, v in FAKE_BIN.items() if k != "sudo"}
        fake = Fake({})
        code, _ = self.call(["--list", self.lst("b.txt", "git\n"), "--install"], fake, euid=1000, which=bare)
        self.assertEqual(code, 2)
        self.assertEqual([argv[0] for argv, _ in fake.calls], ["/fake/dpkg-query"])

    def test_apt_runs_with_bounded_retries_and_timeouts(self):
        fake = Fake({})
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install"], fake)
        self.assertEqual(code, 0)
        for step in result["executed"]:
            for option in ("Acquire::Retries=1", "Acquire::http::Timeout=15",
                           "Acquire::https::Timeout=15", "APT::Update::Error-Mode=any"):
                self.assertEqual(step.count(option), 1, option)

    def test_receipt_equals_report_and_records_before_after_hashes_and_os(self):
        listing = self.lst("a.txt", "git\ntzdata\n")
        receipt = self.root / "receipt.json"
        fake = Fake({"git": "1:2.0"})
        code, result = self.call(["--list", listing, "--install", "--receipt", str(receipt)], fake)
        self.assertEqual((code, result["status"]), (0, "installed"))
        self.assertEqual(json.loads(receipt.read_text(encoding="utf-8")), result)
        self.assertEqual(result["before"], {"git": "1:2.0", "tzdata": None})
        self.assertEqual(result["packages"]["tzdata"]["state"], "ok")
        self.assertEqual(result["list_sha256"], {listing: hashlib.sha256(Path(listing).read_bytes()).hexdigest()})
        self.assertIsInstance(result["os"], dict)
        self.assertGreaterEqual(result["seconds"], 0)

    def test_existing_receipt_or_log_is_refused_before_anything_runs(self):
        listing = self.lst("a.txt", "git\n")
        taken = self.root / "taken.txt"
        taken.write_text("keep", encoding="utf-8")
        for flag in ("--receipt", "--log"):
            fake = Fake({})
            code, _ = self.call(["--list", listing, "--install", flag, str(taken)], fake)
            self.assertEqual((code, fake.calls), (2, []), flag)
        self.assertEqual(taken.read_text(encoding="utf-8"), "keep")

    def test_log_keeps_apt_output_and_prints_only_the_summary(self):
        log = self.root / "apt.log"
        fake = Fake({}, apt_output={
            "update": "Hit:1 http://archive.example/ubuntu noble InRelease\nW: stale list ignored\n",
            "install": "Reading package lists...\n0 upgraded, 1 newly installed, 0 to remove and 3 not upgraded.\n"})
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install", "--log", str(log)], fake)
        self.assertEqual(code, 0)
        self.assertIn("Hit:1", log.read_text(encoding="utf-8"))
        self.assertEqual(result["log"], {"path": str(log), "bytes": log.stat().st_size,
            "summary": "0 upgraded, 1 newly installed, 0 to remove and 3 not upgraded.",
            "warnings": 1, "errors": 0})
        self.assertIn("1 newly installed", self.err.getvalue())
        self.assertNotIn("Hit:1", self.err.getvalue())
        apt_calls = [kwargs for argv, kwargs in fake.calls if argv[0] == "/fake/apt-get"]
        self.assertEqual(len(apt_calls), 2)
        for kwargs in apt_calls:
            self.assertEqual(kwargs["stderr"], subprocess.STDOUT)

    def test_failed_install_shows_the_log_tail_on_stderr(self):
        log = self.root / "apt.log"
        fake = Fake({}, apt_rc=100, apt_output={"update": "Err:1 http://archive.example/ubuntu noble InRelease\nE: Failed to fetch\n"})
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install", "--log", str(log)], fake)
        self.assertEqual((code, result["status"]), (3, "failed"))
        self.assertEqual((result["log"]["errors"], result["log"]["summary"]), (2, None))
        self.assertIn("E: Failed to fetch", self.err.getvalue())

    def test_log_without_install_creates_nothing(self):
        log = self.root / "apt.log"
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--log", str(log)], Fake({}))
        self.assertEqual((code, result["log"]), (1, None))
        self.assertFalse(log.exists())

    def final_state(self, listing, todo, status="downloaded", name="dl.state", **extra):
        digest = hashlib.sha256(Path(listing).read_bytes()).hexdigest()
        state = {"status": status, "to_install": todo, "list_sha256": {listing: digest}}
        state.update(extra)
        path = self.root / name
        path.write_text(json.dumps(state), encoding="utf-8")
        return str(path)

    def test_download_only_fetches_without_installing_or_reverifying(self):
        fake = Fake({"git": "1:2.0"})
        listing = self.lst("a.txt", "git\ntzdata\n")
        code, result = self.call(["--list", listing, "--download-only"], fake)
        self.assertEqual((code, result["status"]), (0, "downloaded"))
        self.assertEqual(result["executed"], [
            apt("update"), apt("install", "-y", "--no-install-recommends", "--download-only", "tzdata")])
        self.assertEqual(result["packages"]["tzdata"]["state"], "missing")  # nothing was installed
        self.assertEqual([argv[0] for argv, _ in fake.calls].count("/fake/dpkg-query"), 1)

    def test_download_only_with_nothing_missing_does_nothing(self):
        fake = Fake({"git": "1:2.0"})
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--download-only"], fake)
        self.assertEqual((code, result["status"], result["executed"]), (0, "satisfied", []))

    def test_download_only_failure_is_exit_3(self):
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--download-only"], Fake({}, apt_rc=100))
        self.assertEqual((code, result["status"]), (3, "failed"))

    def test_child_publishes_running_then_the_final_result_as_state(self):
        state = self.root / "dl.state"
        seen = []

        class Peek(Fake):
            def __call__(self, argv, **kwargs):
                if argv[0] == "/fake/apt-get" and "update" in argv:
                    seen.append(json.loads(state.read_text(encoding="utf-8")))
                return super().__call__(argv, **kwargs)
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--download-only", "--state", str(state)],
                                 Peek({}))
        self.assertEqual((code, seen), (0, [{"status": "running", "pid": os.getpid()}]))
        final = json.loads(state.read_text(encoding="utf-8"))
        self.assertEqual(final, dict(result, pid=os.getpid()))
        self.assertEqual((final["status"], final["to_install"]), ("downloaded", ["git"]))
        self.assertEqual([p.name for p in self.root.iterdir() if p.name.endswith(".tmp")], [])

    def test_child_without_root_or_sudo_leaves_a_failed_state(self):
        state = self.root / "dl.state"
        bare = {k: v for k, v in FAKE_BIN.items() if k != "sudo"}
        code, _ = self.call(["--list", self.lst("a.txt", "git\n"), "--download-only", "--state", str(state)],
                            Fake({}), euid=1000, which=bare)
        self.assertEqual((code, json.loads(state.read_text(encoding="utf-8"))["status"]), (2, "failed"))

    def test_background_starts_a_detached_download_and_returns_at_once(self):
        listing, state = self.lst("a.txt", "git\n"), self.root / "dl.state"
        started = []

        class Proc:
            pid = 4242

        def popen(argv, **kwargs):
            started.append((argv, kwargs))
            return Proc()
        fake = Fake({})
        with patch.object(nano.subprocess, "Popen", popen):
            code, result = self.call(["--list", listing, "--background", "--state", str(state)], fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.calls, [])  # the starter itself runs no apt and no dpkg-query
        argv, kwargs = started[0]
        self.assertEqual(argv, [sys.executable, "-S", os.path.abspath(nano.__file__), "--list", listing,
                                "--download-only", "--state", str(state)])
        self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(result, {"schema": nano.SCHEMA, "status": "started", "pid": 4242, "state": str(state),
                                  "log": str(state) + ".log", "lists": [listing]})
        self.assertEqual(json.loads(state.read_text(encoding="utf-8")), {"status": "starting"})
        self.assertEqual(Path(str(state) + ".pid").read_text(encoding="utf-8"), "4242")

    def test_background_refuses_an_existing_state_and_cleans_up_when_spawn_fails(self):
        listing, state = self.lst("a.txt", "git\n"), self.root / "dl.state"
        state.write_text("keep", encoding="utf-8")
        with patch.object(nano.subprocess, "Popen", side_effect=AssertionError("must not start")):
            code, _ = self.call(["--list", listing, "--background", "--state", str(state)], Fake({}))
        self.assertEqual((code, state.read_text(encoding="utf-8")), (2, "keep"))
        state.unlink()
        with patch.object(nano.subprocess, "Popen", side_effect=OSError("no exec")):
            code, _ = self.call(["--list", listing, "--background", "--state", str(state)], Fake({}))
        self.assertEqual((code, state.exists()), (2, False))

    def test_background_refuses_each_existing_output_without_spawning(self):
        listing = self.lst("a.txt", "git\n")
        for suffix in ("", ".log", ".pid"):
            for kind in ("file", "symlink", "dangling", "directory"):
                with self.subTest(suffix=suffix, kind=kind):
                    folder = self.root / (kind + (suffix or ".state"))
                    folder.mkdir()
                    state = folder / "dl.state"
                    taken = Path(str(state) + suffix)
                    target = folder / "target"
                    if kind == "file":
                        taken.write_text("keep", encoding="utf-8")
                    elif kind == "directory":
                        taken.mkdir()
                    else:
                        if kind == "symlink":
                            target.write_text("keep", encoding="utf-8")
                        taken.symlink_to(target)
                    fake = Fake({})
                    with patch.object(nano.subprocess, "Popen") as spawn:
                        code, _ = self.call(["--list", listing, "--background", "--state", str(state)], fake)
                    self.assertEqual((code, fake.calls), (2, []))
                    spawn.assert_not_called()
                    self.assertEqual(sorted(p.name for p in folder.glob("dl.state*")), [taken.name])
                    if kind == "file":
                        self.assertEqual(taken.read_text(encoding="utf-8"), "keep")
                    elif kind == "symlink":
                        self.assertTrue(taken.is_symlink())
                        self.assertEqual(target.read_text(encoding="utf-8"), "keep")
                    elif kind == "dangling":
                        self.assertTrue(taken.is_symlink())
                        self.assertFalse(target.exists())

    def test_background_exclusive_creation_handles_a_preflight_race(self):
        listing = self.lst("a.txt", "git\n")
        for suffix in ("", ".log", ".pid"):
            with self.subTest(suffix=suffix):
                state = self.root / ("race" + (suffix or ".state"))
                taken = Path(str(state) + suffix)
                real_start = nano.start_background

                def race(args):
                    taken.write_text("racing writer", encoding="utf-8")
                    return real_start(args)

                with patch.object(nano, "start_background", race), \
                        patch.object(nano.subprocess, "Popen") as spawn:
                    code, _ = self.call(["--list", listing, "--background", "--state", str(state)], Fake({}))
                self.assertEqual(code, 2)
                spawn.assert_not_called()
                self.assertEqual(taken.read_text(encoding="utf-8"), "racing writer")
                self.assertEqual(sorted(p.name for p in self.root.glob(state.name + "*")), [taken.name])

    def test_background_spawn_failure_removes_all_reserved_outputs_and_can_retry(self):
        listing, state = self.lst("a.txt", "git\n"), self.root / "dl.state"
        args = ["--list", listing, "--background", "--state", str(state)]
        with patch.object(nano.subprocess, "Popen", side_effect=OSError("no exec")):
            code, _ = self.call(args, Fake({}))
        self.assertEqual(code, 2)
        self.assertEqual(list(self.root.glob("dl.state*")), [])
        with patch.object(nano.subprocess, "Popen") as spawn:
            spawn.return_value.pid = 4242
            code, _ = self.call(args, Fake({}))
        self.assertEqual(code, 0)
        self.assertEqual(Path(str(state) + ".pid").read_text(encoding="utf-8"), "4242")

    def test_background_failed_spawn_cleanup_tolerates_missing_or_replaced_output(self):
        listing = self.lst("a.txt", "git\n")
        for replaced in (False, True):
            with self.subTest(replaced=replaced):
                state = self.root / ("replaced" if replaced else "missing")
                pid = Path(str(state) + ".pid")

                def fail_spawn(*args, **kwargs):
                    # Keep the original inode alive while replacing its name.
                    pid.rename(self.root / "old-pid")
                    if replaced:
                        pid.write_text("keep replacement", encoding="utf-8")
                    raise OSError("no exec")

                with patch.object(nano.subprocess, "Popen", fail_spawn):
                    code, _ = self.call(["--list", listing, "--background", "--state", str(state)], Fake({}))
                self.assertEqual(code, 2)
                self.assertFalse(state.exists())
                self.assertFalse(Path(str(state) + ".log").exists())
                if replaced:
                    self.assertEqual(pid.read_text(encoding="utf-8"), "keep replacement")
                else:
                    self.assertFalse(pid.exists())

    def test_background_flag_combinations_are_usage_errors(self):
        listing, state = self.lst("a.txt", "git\n"), str(self.root / "dl.state")
        for extra in (["--background"], ["--background", "--state", state, "--install"],
                      ["--background", "--state", state, "--receipt", str(self.root / "r.json")],
                      ["--background", "--state", state, "--strict"], ["--download-only", "--install"]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit) as caught:
                self.call(["--list", listing] + extra, Fake({}))
            self.assertEqual(caught.exception.code, 2)
        self.assertEqual(list(self.root.glob("dl.state*")), [])

    def test_install_joins_a_matching_download_and_skips_update(self):
        listing = self.lst("a.txt", "git\ntzdata\n")
        state = self.final_state(listing, ["git", "tzdata"])
        code, result = self.call(["--list", listing, "--install", "--state", state], Fake({}))
        self.assertEqual((code, result["status"]), (0, "installed"))
        self.assertEqual(result["executed"], [apt("install", "-y", "--no-install-recommends", "git", "tzdata")])
        self.assertEqual((result["joined"]["state"], result["joined"]["skipped_update"]), ("downloaded", True))

    def test_install_does_not_trust_a_download_of_something_else(self):
        listing = self.lst("a.txt", "git\ntzdata\n")
        cases = {"other packages": self.final_state(listing, ["git"], name="other.state"),
                 "download failed": self.final_state(listing, ["git", "tzdata"], status="failed", name="failed.state")}
        for label, state in cases.items():
            with self.subTest(label), patch.object(nano, "WAIT_SECONDS", 1):
                code, result = self.call(["--list", listing, "--install", "--state", state], Fake({}))
                self.assertEqual((code, result["status"]), (0, "installed"))
                self.assertEqual(result["executed"][0], apt("update"))
                self.assertFalse(result["joined"]["skipped_update"])
        state = self.final_state(listing, ["git", "tzdata"], name="edited.state")
        Path(listing).write_text("# edited\ngit\ntzdata\n", encoding="utf-8")  # same packages, different file
        code, result = self.call(["--list", listing, "--install", "--state", state], Fake({}))
        self.assertEqual(result["executed"][0], apt("update"))
        self.assertFalse(result["joined"]["skipped_update"])

    def test_install_without_a_usable_state_falls_back_to_the_full_path(self):
        listing = self.lst("a.txt", "git\n")
        gone, junk = str(self.root / "missing.state"), self.root / "junk.state"
        junk.write_text("{not json", encoding="utf-8")
        for state in (gone, str(junk)):
            with self.subTest(state=state):
                code, result = self.call(["--list", listing, "--install", "--state", state], Fake({}))
                self.assertEqual((code, result["executed"][0]), (0, apt("update")))
                self.assertEqual(result["joined"]["state"], "none")

    def test_install_does_not_wait_for_a_dead_download(self):
        listing = self.lst("a.txt", "git\n")
        state = self.final_state(listing, ["git"], status="running", pid=99999999)
        Path(state + ".pid").write_text("99999999", encoding="utf-8")
        with patch.object(nano, "alive", lambda pid: False), patch.object(nano, "WAIT_SECONDS", 30):
            code, result = self.call(["--list", listing, "--install", "--state", state], Fake({}))
        self.assertEqual((code, result["executed"][0]), (0, apt("update")))
        self.assertEqual(result["joined"]["state"], "failed")
        self.assertLess(result["joined"]["waited"], 5)

    def test_install_stops_waiting_after_the_limit(self):
        listing = self.lst("a.txt", "git\n")
        state = self.final_state(listing, ["git"], status="running")
        Path(state + ".pid").write_text(str(os.getpid()), encoding="utf-8")
        with patch.object(nano, "WAIT_SECONDS", 0.3), patch.object(nano, "POLL_SECONDS", 0.05):
            code, result = self.call(["--list", listing, "--install", "--state", state], Fake({}))
        self.assertEqual((code, result["executed"][0]), (0, apt("update")))
        self.assertEqual(result["joined"]["state"], "timeout")
        self.assertGreaterEqual(result["joined"]["waited"], 0.3)

    def test_install_waits_for_a_running_download_then_skips_update(self):
        listing = self.lst("a.txt", "git\n")
        state = self.final_state(listing, ["git"], status="running")
        Path(state + ".pid").write_text(str(os.getpid()), encoding="utf-8")
        done = json.loads(Path(state).read_text(encoding="utf-8"))
        done["status"] = "downloaded"
        timer = threading.Timer(0.3, nano.write_state, (state, done))
        timer.start()
        self.addCleanup(timer.join)
        with patch.object(nano, "POLL_SECONDS", 0.05):
            code, result = self.call(["--list", listing, "--install", "--state", state], Fake({}))
        self.assertEqual((code, result["executed"]), (0, [apt("install", "-y", "--no-install-recommends", "git")]))
        self.assertGreaterEqual(result["joined"]["waited"], 0.2)
        self.assertTrue(result["joined"]["skipped_update"])

    def test_strict_refuses_a_missing_apt_toolchain_and_empty_lists(self):
        listing, empty = self.lst("a.txt", "git\n"), self.lst("empty.txt", "# nothing yet\n\n")
        fake = Fake({})
        code, result = self.call(["--list", listing, "--install", "--strict"], fake, which={})
        self.assertEqual((code, result["status"], fake.calls), (2, "failed", []))
        self.assertIn("--strict", result["error"])
        fake = Fake({})
        code, result = self.call(["--list", empty, "--install", "--strict"], fake)
        self.assertEqual((code, result, fake.calls), (2, None, []))
        self.assertIn("no packages listed", self.err.getvalue())

    def test_strict_changes_nothing_when_apt_works_and_lax_mode_keeps_its_exit_codes(self):
        listing, empty = self.lst("a.txt", "git\n"), self.lst("empty.txt", "# nothing yet\n")
        code, result = self.call(["--list", listing, "--install", "--strict"], Fake({"git": "1:2.0"}))
        self.assertEqual((code, result["status"]), (0, "satisfied"))
        code, result = self.call(["--list", listing, "--install", "--strict"], Fake({}))
        self.assertEqual((code, result["status"]), (0, "installed"))
        self.assertEqual(self.call(["--list", empty], Fake({}))[0], 0)
        self.assertEqual(self.call(["--list", listing, "--install"], Fake({}), which={})[1]["status"], "apt_unavailable")

    def archive_fake(self, archives, new_debs):
        """Fake apt that 'downloads' new_debs into the --archives directory on install."""
        outer = self

        class Downloads(Fake):
            def __call__(self, argv, **kwargs):
                if "install" in argv:
                    for name in new_debs:
                        (archives / name).write_bytes(b"deb")
                return super().__call__(argv, **kwargs)
        return Downloads({})

    def test_archives_keeps_downloads_in_dir_and_reports_a_changed_set(self):
        archives = self.root / "apt-archives"
        out = self.root / "github-output"
        out.write_text("earlier=1\n")
        fake = self.archive_fake(archives, ["git_1_amd64.deb"])
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install", "--archives", str(archives),
                                  "--github-output", str(out)], fake)
        self.assertEqual((code, result["status"]), (0, "installed"))
        option = ["-o", "Dir::Cache::Archives=%s/" % archives.resolve()]
        self.assertEqual(result["executed"], [
            apt("update"), apt(*option, "install", "-y", "--no-install-recommends", "git")])
        autoclean = [argv for argv, _ in fake.calls if "autoclean" in argv]
        self.assertEqual(autoclean, [["/fake/apt-get"] + option + ["autoclean", "-qq"]])
        self.assertEqual(result["archives"], {"dir": str(archives.resolve()), "restored": 0, "debs": 1,
                                              "changed": True, "autoclean": 0})
        self.assertEqual(sorted(p.name for p in archives.iterdir()), ["git_1_amd64.deb"])  # no partial/ or lock
        self.assertFalse((self.root / "apt-archives.restored").exists())  # a later run compares afresh
        self.assertEqual(out.read_text(), "earlier=1\narchives_changed=true\n")

    def test_download_then_install_compares_with_what_was_there_before_the_download(self):
        archives = self.root / "apt-archives"
        listing = self.lst("a.txt", "git\n")
        fake = self.archive_fake(archives, ["git_1_amd64.deb"])
        code, result = self.call(["--list", listing, "--download-only", "--archives", str(archives)], fake)
        self.assertEqual((code, result["archives"]["changed"]), (0, True))
        self.assertEqual((self.root / "apt-archives.restored").read_text(), "")  # kept for the join
        code, result = self.call(["--list", listing, "--install", "--archives", str(archives)], fake)
        self.assertEqual((code, result["archives"]["restored"], result["archives"]["changed"]), (0, 0, True))
        self.assertFalse((self.root / "apt-archives.restored").exists())

    def test_archives_restored_unchanged_is_not_a_change(self):
        archives = self.root / "apt-archives"
        archives.mkdir()
        (archives / "git_1_amd64.deb").write_bytes(b"deb")
        (archives / "lock").write_text("")
        out = self.root / "github-output"
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install", "--archives", str(archives),
                                  "--github-output", str(out)], self.archive_fake(archives, ["git_1_amd64.deb"]))
        self.assertEqual(code, 0)
        self.assertEqual((result["archives"]["restored"], result["archives"]["changed"]), (1, False))
        self.assertEqual(out.read_text(), "archives_changed=false\n")
        self.assertFalse((archives / "lock").exists())

    def test_archives_with_nothing_missing_runs_no_apt_and_is_unchanged(self):
        out = self.root / "github-output"
        fake = Fake({"git": "1"})
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install",
                                  "--archives", str(self.root / "apt-archives"), "--github-output", str(out)], fake)
        self.assertEqual((code, result["status"], result["archives"]["changed"]), (0, "satisfied", False))
        self.assertEqual([argv[0] for argv, _ in fake.calls], ["/fake/dpkg-query"])
        self.assertEqual(out.read_text(), "archives_changed=false\n")

    def test_archives_as_non_root_cleans_apt_work_files_through_sudo(self):
        archives = self.root / "apt-archives"
        fake = self.archive_fake(archives, ["git_1_amd64.deb"])
        with patch.object(nano.shutil, "which", lambda name, path=None: dict(FAKE_BIN, rm="/fake/rm").get(name)):
            code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install", "--archives", str(archives)],
                                     fake, euid=1000, which=dict(FAKE_BIN, rm="/fake/rm"))
        self.assertEqual(code, 0)
        removal = [argv for argv, _ in fake.calls if "/fake/rm" in argv]
        self.assertEqual(removal, [["/fake/sudo", "-n", "/fake/rm", "-rf", "--",
                                    str(archives.resolve() / "partial"), str(archives.resolve() / "lock")]])
        self.assertIn(["/fake/sudo", "-n", "/fake/apt-get", "-o", "Dir::Cache::Archives=%s/" % archives.resolve(),
                       "autoclean", "-qq"], [argv for argv, _ in fake.calls])

    def test_archives_and_github_output_usage_errors(self):
        listing = self.lst("a.txt", "git\n")
        for args in (["--archives", "d"], ["--github-output", "f"], ["--install", "--github-output", "f"],
                     ["--background", "--state", "s", "--github-output", "f"]):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                self.call(["--list", listing] + args, Fake({}))

    def test_background_passes_archives_to_the_download(self):
        state = str(self.root / "apt.state")
        with patch.object(nano.subprocess, "Popen") as popen, contextlib.redirect_stdout(io.StringIO()):
            popen.return_value.pid = 4242
            self.assertEqual(nano.main(["--list", self.lst("a.txt", "git\n"), "--background", "--state", state,
                                        "--archives", "cache"]), 0)
        argv = popen.call_args[0][0]
        self.assertEqual(argv[-4:], ["--state", state, "--archives", "cache"])

    def test_without_apt_nothing_runs(self):
        fake = Fake({})
        code, result = self.call(["--list", self.lst("a.txt", "git\n"), "--install"], fake, which={})
        self.assertEqual((code, result["status"]), (0, "apt_unavailable"))
        self.assertEqual(fake.calls, [])

    @unittest.skipUnless(shutil.which("dpkg-query") and shutil.which("apt-get"), "needs Debian/Ubuntu")
    def test_real_dpkg_query_is_read_only(self):
        have = self.lst("have.txt", "dpkg\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(nano.main(["--list", have, "--install"]), 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "satisfied")
        receipt = self.root / "real-receipt.json"
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(nano.main(["--list", have, "--receipt", str(receipt)]), 0)
        saved = json.loads(receipt.read_text(encoding="utf-8"))
        self.assertEqual(saved, json.loads(out.getvalue()))
        self.assertIsNotNone(saved["before"]["dpkg"])
        self.assertTrue(saved["os"].get("id"))
        lack = self.lst("lack.txt", "dpkg\nnano-apt-no-such-package\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(nano.main(["--list", lack]), 1)
        self.assertEqual(json.loads(out.getvalue())["to_install"], ["nano-apt-no-such-package"])


# ═══ · nano apt adoption ═════════════════════════════════════════════════════════════════════════════════
# Offline adoption experiment for Ironmate #203 at 77b4a584795c.
#
# Install commands are mocked; one guarded inherited observation runs real dpkg-query.
# Host apt and other mutating commands are prohibited.

class AdoptionTests(NanoAptTests):
    def groups(self):
        return [self.lst('fonts.txt', 'fonts-noto-cjk\n'),
                self.lst('uno.txt', 'python3-uno\nlibreoffice-impress\n')]

    def test_exact_groups_and_duplicate_elimination(self):
        fonts, uno = self.groups()
        self.assertEqual(list(nano.read_list(fonts)), ['fonts-noto-cjk'])
        self.assertEqual(list(nano.read_list(uno)), ['python3-uno', 'libreoffice-impress'])
        self.assertEqual(list(nano.merge([fonts, uno, fonts])),
                         ['fonts-noto-cjk', 'python3-uno', 'libreoffice-impress'])

    def test_separate_calls_preserve_groups_but_update_twice(self):
        fake = Fake({})
        for group in self.groups():
            self.assertEqual(self.call(['--list', group, '--install'], fake)[0], 0)
        apt = [argv for argv, _ in fake.calls if argv[0].endswith('apt-get')]
        self.assertEqual(sum(argv[-1] == 'update' for argv in apt), 2)
        self.assertEqual(apt[1][-1:], ['fonts-noto-cjk'])
        self.assertEqual(apt[3][-2:], ['libreoffice-impress', 'python3-uno'])

    def test_merged_call_updates_once_but_sorts_across_groups(self):
        fonts, uno = self.groups()
        fake = Fake({})
        self.assertEqual(self.call(['--list', fonts, '--list', uno, '--install'], fake)[0], 0)
        apt = [argv for argv, _ in fake.calls if argv[0].endswith('apt-get')]
        self.assertEqual(len(apt), 2)
        self.assertEqual(apt[-1][-3:], ['fonts-noto-cjk', 'libreoffice-impress', 'python3-uno'])

    def test_second_run_never_repeats_apt(self):
        fonts, uno = self.groups()
        fake = Fake({})
        args = ['--list', fonts, '--list', uno, '--install']
        self.assertEqual(self.call(args, fake)[0], 0)
        fake.calls.clear()
        self.assertEqual(self.call(args, fake)[0], 0)
        self.assertTrue(all(argv[0].endswith('dpkg-query') for argv, _ in fake.calls))

    def test_install_failure_is_code_three(self):
        class InstallFailure(Fake):
            def __call__(self, argv, **kw):
                if 'install' in argv:
                    self.calls.append((list(argv), kw))
                    return subprocess.CompletedProcess(argv, 100)
                return super().__call__(argv, **kw)
        fake = InstallFailure({})
        code, result = self.call(['--list', self.groups()[0], '--install'], fake)
        self.assertEqual((code, result['status']), (3, 'failed'))
        self.assertEqual(len(fake.calls), 3)

    def test_empty_list_currently_succeeds(self):
        code, result = self.call(['--list', self.lst('empty.txt', '# comment\n'), '--install'], Fake({}))
        self.assertEqual((code, result['status']), (0, 'satisfied'))

    def test_query_failure_stops_before_install(self):
        def broken(argv, **kw):
            return subprocess.CompletedProcess(argv, 2, stdout='', stderr='database error')
        calls = []
        def guarded(argv, **kw):
            calls.append(list(argv))
            self.assertTrue(argv[0].endswith('dpkg-query'))
            return broken(argv, **kw)
        code, result = self.call(['--list', self.groups()[0], '--install'], guarded)
        self.assertEqual((code, result['status']), (2, 'failed'))
        self.assertEqual(len(calls), 1)

    def test_real_dpkg_query_is_read_only(self):
        real_run = subprocess.run
        def readonly(argv, **kw):
            if not argv[0].endswith('dpkg-query'):
                raise RuntimeError('host mutation prohibited')
            return real_run(argv, **kw)
        with patch.object(nano.subprocess, 'run', readonly):
            super().test_real_dpkg_query_is_read_only()


# ═══ · nano apt query failure ════════════════════════════════════════════════════════════════════════════
# Fatal dpkg observation must never trigger installation.

class QueryFailureTests(unittest.TestCase):
    setUp = NanoAptTests.setUp
    lst = NanoAptTests.lst
    call = NanoAptTests.call

    def test_fatal_query_blocks_report_install_and_download(self):
        for mode in ([], ['--install'], ['--download-only']):
            calls = []
            def fail(argv, **kwargs):
                calls.append(argv)
                if not argv[0].endswith('dpkg-query'):
                    self.fail('apt must not run after observation failure')
                return subprocess.CompletedProcess(argv, 2, stdout='', stderr='database unreadable')
            with self.subTest(mode=mode):
                code, report = self.call(['--list', self.lst('packages.txt', 'git\n')] + mode, fail)
                self.assertEqual(code, 2)
                self.assertEqual(report['status'], 'failed')
                self.assertEqual(report['executed'], [])
                self.assertIn('dpkg-query failed', report['error'])
                self.assertEqual(len(calls), 1)

    def test_post_install_query_failure_preserves_before_and_receipt(self):
        queries = []
        def run(argv, **kwargs):
            if argv[0].endswith('dpkg-query'):
                queries.append(argv)
                return subprocess.CompletedProcess(
                    argv, 0 if len(queries) == 1 else 2,
                    stdout='git ii 1.0\n' if len(queries) == 1 else '', stderr='')
            return subprocess.CompletedProcess(argv, 0)
        receipt = self.root / 'receipt.json'
        code, report = self.call(['--list', self.lst('packages.txt', 'git=2.0\n'),
                                  '--install', '--receipt', str(receipt)], run)
        self.assertEqual(code, 2)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['before'], {'git': '1.0'})
        self.assertEqual(len(report['executed']), 2)
        self.assertEqual(len(queries), 2)
        self.assertEqual(json.loads(receipt.read_text()), report)

    def test_failed_download_query_publishes_failed_state(self):
        state = self.root / 'download.json'
        def run(argv, **kwargs):
            if not argv[0].endswith('dpkg-query'):
                self.fail('no apt command expected')
            return subprocess.CompletedProcess(argv, 2, stdout='', stderr='')
        code, report = self.call(['--list', self.lst('packages.txt', 'git\n'),
                                  '--download-only', '--state', str(state)], run)
        self.assertEqual(code, 2)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(json.loads(state.read_text())['status'], 'failed')
        self.assertEqual(report['executed'], [])

    def test_query_spawn_error_is_reported_without_apt(self):
        for exception in (FileNotFoundError('executable disappeared'),
                          PermissionError('executable denied')):
            for mode in ([], ['--install'], ['--download-only']):
                calls = []
                def run(argv, **kwargs):
                    calls.append(argv)
                    if not argv[0].endswith('dpkg-query'):
                        self.fail('apt must not run when observation cannot start')
                    raise exception
                with self.subTest(exception=type(exception).__name__, mode=mode):
                    code, report = self.call(['--list', self.lst('packages.txt', 'git\n')] + mode, run)
                    self.assertEqual(code, 2)
                    self.assertEqual(report['status'], 'failed')
                    self.assertIn('dpkg-query could not start', report['error'])
                    self.assertEqual(report['executed'], [])
                    self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
